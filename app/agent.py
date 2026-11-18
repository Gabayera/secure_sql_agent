from __future__ import annotations

import uuid
from typing import Any

from sqlglot import exp

from app.llm.base import LLMClient, LLMError
from app.models import (
    AgentResponse,
    ApprovalRequest,
    AuditRecord,
    ClassificationResult,
    GeneratedSQL,
    ValidationResult,
)
from app.security.approval import ApprovalError, ApprovalManager, ApprovalProvider
from app.security.audit import AuditLogger
from app.sql.classifier import SQLClassifier
from app.sql.live_validator import LiveValidator
from app.sql.parser import SQLParseError, SQLParser
from app.sql.safety import is_potentially_unbounded_write
from app.sql.schema_validator import SchemaValidator


class SQLAgent:
    def __init__(
        self,
        llm: LLMClient,
        schema_manager: Any,
        parser: SQLParser,
        classifier: SQLClassifier,
        schema_validator: SchemaValidator,
        live_validator: LiveValidator | None,
        executor: Any,
        approval_manager: ApprovalManager,
        audit: AuditLogger | None,
        max_repair_attempts: int = 2,
        approval_provider: ApprovalProvider | None = None,
    ) -> None:
        self.llm = llm
        self.schema_manager = schema_manager
        self.parser = parser
        self.classifier = classifier
        self.schema_validator = schema_validator
        self.live_validator = live_validator
        self.executor = executor
        self.approval_manager = approval_manager
        self.approval_provider = approval_provider or approval_manager.provider
        self.audit = audit
        self.max_repair_attempts = max_repair_attempts

    def process_request(self, request: str, user: str | None = None) -> AgentResponse:
        request_id = str(uuid.uuid4())
        audit = AuditRecord(
            request_id=request_id,
            user=user,
            natural_language_request=request,
        )
        try:
            response = self._process(request, request_id, audit)
        except Exception as exc:
            audit.error = str(exc)
            response = AgentResponse(
                status="error",
                request_id=request_id,
                message=f"Unexpected error: {exc}",
            )
        if self.audit is not None:
            self.audit.log(audit)
        return response

    def _process(
        self, request: str, request_id: str, audit: AuditRecord
    ) -> AgentResponse:
        schema = self.schema_manager.get_schema()
        generated = self.llm.generate_sql(request, schema)
        audit.sql = generated.query
        audit.parameters = generated.parameters

        classification: ClassificationResult | None = None
        validation: ValidationResult | None = None
        tree: exp.Expression | None = None

        for attempt in range(self.max_repair_attempts + 1):
            tree, classification, validation = self._validate_generated(
                generated, schema
            )
            if validation.valid:
                break
            if attempt >= self.max_repair_attempts:
                audit.validation_result = validation.summary()
                audit.error = validation.summary()
                return AgentResponse(
                    status="validation_failed",
                    request_id=request_id,
                    message=(
                        "The generated SQL could not be validated after "
                        f"{self.max_repair_attempts + 1} attempt(s): "
                        f"{validation.summary()}"
                    ),
                    generated_sql=generated,
                    classification=classification,
                    validation=validation,
                )
            try:
                generated = self.llm.generate_sql(
                    request, schema, error_context=validation.summary()
                )
                audit.sql = generated.query
                audit.parameters = generated.parameters
            except LLMError as exc:
                return AgentResponse(
                    status="error",
                    request_id=request_id,
                    message=f"SQL regeneration failed: {exc}",
                    generated_sql=generated,
                )

        assert tree is not None and classification is not None
        assert validation is not None
        audit.operation_type = classification.operation
        audit.tables = classification.tables
        audit.validation_result = "PASS"

        if classification.kind == "read":
            execution = self.executor.execute_read(
                generated.query, generated.parameters
            )
            audit.execution_success = execution.success
            if not execution.success:
                audit.error = execution.error
                return AgentResponse(
                    status="error",
                    request_id=request_id,
                    message=f"Read query failed: {execution.error}",
                    generated_sql=generated,
                    classification=classification,
                    validation=validation,
                    execution=execution,
                )
            return AgentResponse(
                status="success",
                request_id=request_id,
                message=(
                    f"Query executed successfully. "
                    f"{execution.row_count} row(s) returned"
                    + (" (truncated)." if execution.truncated else ".")
                ),
                generated_sql=generated,
                classification=classification,
                validation=validation,
                execution=execution,
            )

        if classification.kind not in ("write", "ddl"):
            return AgentResponse(
                status="rejected",
                request_id=request_id,
                message=classification.reason or "Unsupported statement",
                generated_sql=generated,
                classification=classification,
                validation=validation,
            )

        if self.approval_provider is None:
            audit.approval_decision = "UNAVAILABLE"
            return AgentResponse(
                status="rejected",
                request_id=request_id,
                message=(
                    "This operation modifies data, but no human approval "
                    "mechanism is available. It has been rejected."
                ),
                generated_sql=generated,
                classification=classification,
                validation=validation,
                approval_required=True,
            )

        if is_potentially_unbounded_write(tree):
            audit.approval_decision = "REJECTED_UNBOUNDED"
            return AgentResponse(
                status="rejected",
                request_id=request_id,
                message="UPDATE/DELETE without a WHERE clause is rejected by policy.",
                generated_sql=generated,
                classification=classification,
                validation=validation,
            )

        while True:
            approval_request = ApprovalRequest(
                request_id=request_id,
                operation=classification.operation,
                sql=generated.query,
                parameters=generated.parameters,
                explanation=generated.explanation,
                tables=classification.tables,
            )
            decision = self.approval_provider.request(approval_request)

            if decision.edited_sql:
                generated = GeneratedSQL(
                    query=decision.edited_sql,
                    parameters=decision.edited_parameters or {},
                    explanation="User-edited SQL",
                )
                audit.sql = generated.query
                audit.parameters = generated.parameters
                tree, classification, validation = self._validate_generated(
                    generated, schema
                )
                if not validation.valid:
                    audit.approval_decision = "EDITED_INVALID"
                    audit.validation_result = validation.summary()
                    return AgentResponse(
                        status="validation_failed",
                        request_id=request_id,
                        message=(
                            "The edited SQL failed validation and was not executed: "
                            f"{validation.summary()}"
                        ),
                        generated_sql=generated,
                        classification=classification,
                        validation=validation,
                    )
                continue

            if not decision.approved:
                audit.approval_decision = "REJECTED"
                return AgentResponse(
                    status="rejected",
                    request_id=request_id,
                    message="The write operation was rejected by the user.",
                    generated_sql=generated,
                    classification=classification,
                    validation=validation,
                    approval_required=True,
                )

            audit.approval_decision = "APPROVED"
            token = self.approval_manager.create_token(approval_request)
            execution = self.executor.execute_write(
                generated.query, generated.parameters, token.token
            )
            audit.execution_success = execution.success
            audit.affected_rows = execution.affected_rows
            if not execution.success:
                audit.error = execution.error
                return AgentResponse(
                    status="error",
                    request_id=request_id,
                    message=f"Approved write failed: {execution.error}",
                    generated_sql=generated,
                    classification=classification,
                    validation=validation,
                    execution=execution,
                )
            return AgentResponse(
                status="success",
                request_id=request_id,
                message=(
                    "Write operation executed successfully. "
                    f"Affected rows: {execution.affected_rows}."
                ),
                generated_sql=generated,
                classification=classification,
                validation=validation,
                execution=execution,
            )

    def _validate_generated(
        self, generated: GeneratedSQL, schema: dict
    ) -> tuple[exp.Expression, ClassificationResult, ValidationResult]:
        try:
            tree = self.parser.parse_single(generated.query)
        except SQLParseError as exc:
            classification = ClassificationResult(
                kind="unsupported", operation="PARSE_ERROR", reason=str(exc)
            )
            validation = ValidationResult(valid=False, stage="parser")
            validation.add("parse_error", str(exc))
            return exp.Command(this="PARSE_ERROR"), classification, validation

        classification = self.classifier.classify(tree)
        validation = self.schema_validator.validate(tree, classification, schema)
        if not validation.valid:
            return tree, classification, validation

        if classification.kind == "read" and self.live_validator is not None:
            live_result = self.live_validator.validate(
                tree, generated.query, generated.parameters, classification
            )
            if not live_result.valid:
                return tree, classification, live_result

        return tree, classification, validation
