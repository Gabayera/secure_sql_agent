from __future__ import annotations

import json
import uuid
from typing import Any

from sqlglot import exp

from app.llm.base import LLMClient, LLMError
from app.models import (
    AgentResponse,
    ApprovalDecision,
    ApprovalRequest,
    AuditRecord,
    ClassificationResult,
    GeneratedSQL,
    ValidationResult,
)
from app.security.approval import ApprovalManager, ApprovalProvider
from app.security.audit import AuditLogger
from app.sql.classifier import READ_ROOTS, SQLClassifier
from app.sql.live_validator import LiveValidator
from app.sql.params import check_parameters
from app.sql.parser import SQLParseError, SQLParser
from app.sql.safety import is_potentially_unbounded_write
from app.sql.schema_validator import SchemaValidator

Validated = tuple[exp.Expression, ClassificationResult, ValidationResult, GeneratedSQL]


class _ApproveOnce:
    """Approval provider for a decision that was already made elsewhere
    (e.g. the user clicked "Run change" in the web UI)."""

    def request(self, request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(approved=True)


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

    # ------------------------------------------------------------ public API

    def process_request(self, request: str, user: str | None = None) -> AgentResponse:
        """Natural language -> validated SQL -> execution (writes need approval)."""
        return self._run(request, user, execute=True)

    def generate_only(self, request: str, user: str | None = None) -> AgentResponse:
        """Natural language -> validated SQL. Nothing is executed."""
        return self._run(request, user, execute=False)

    def execute_sql(
        self,
        generated: GeneratedSQL,
        user: str | None = None,
        question: str | None = None,
        approved: bool = False,
        execute: bool = True,
    ) -> AgentResponse:
        """Validate (and optionally run) SQL that did not come from the LLM
        in this call: SQL written by a person, or SQL generated earlier.

        * Every check of the normal pipeline runs again (no LLM call).
        * Reads run immediately when ``execute`` is True.
        * Writes return ``pending_approval`` unless ``approved`` is True,
          in which case they go through the normal token-bound write path.
        """
        request_id = str(uuid.uuid4())
        audit = AuditRecord(
            request_id=request_id,
            user=user,
            natural_language_request=question or "(SQL entered directly)",
            mode="direct_sql" if execute else "direct_sql_validate",
        )
        return self._audited(
            audit,
            lambda: self._execute_sql(generated, request_id, audit, approved, execute),
        )

    def record_rejection(
        self,
        generated: GeneratedSQL,
        user: str | None = None,
        question: str | None = None,
    ) -> None:
        """Write an audit record for a write the user declined."""
        audit = AuditRecord(
            request_id=str(uuid.uuid4()),
            user=user,
            natural_language_request=question or "(SQL entered directly)",
            mode="approval",
            sql=generated.query,
            parameters=generated.parameters,
            approval_decision="REJECTED",
            execution_success=False,
        )
        self._log(audit)

    # -------------------------------------------------------------- pipeline

    def _run(self, request: str, user: str | None, execute: bool) -> AgentResponse:
        request_id = str(uuid.uuid4())
        audit = AuditRecord(
            request_id=request_id,
            user=user,
            natural_language_request=request,
            mode="execute" if execute else "generate_only",
        )
        return self._audited(
            audit, lambda: self._process(request, request_id, audit, execute)
        )

    def _audited(self, audit: AuditRecord, fn) -> AgentResponse:
        try:
            response = fn()
        except Exception as exc:
            audit.error = str(exc)
            response = AgentResponse(
                status="error",
                request_id=audit.request_id,
                message=f"Unexpected error: {exc}",
            )
        self._log(audit)
        return response

    def _log(self, audit: AuditRecord) -> None:
        if self.audit is not None:
            try:
                self.audit.log(audit)
            except Exception:
                pass  # auditing must never crash the agent

    def _execute_sql(
        self,
        generated: GeneratedSQL,
        request_id: str,
        audit: AuditRecord,
        approved: bool,
        execute: bool,
    ) -> AgentResponse:
        schema = self.schema_manager.get_schema()
        audit.sql = generated.query
        audit.parameters = generated.parameters

        tree, classification, validation, generated = self._validate_generated(
            generated, schema
        )
        audit.parameters = generated.parameters
        audit.operation_type = classification.operation
        audit.tables = classification.tables

        if not validation.valid:
            audit.validation_result = validation.summary()
            audit.error = validation.summary()
            is_policy = validation.stage == "policy"
            return AgentResponse(
                status="rejected" if is_policy else "validation_failed",
                request_id=request_id,
                message=(
                    f"Rejected by security policy: {classification.reason or validation.summary()}"
                    if is_policy
                    else f"The SQL did not pass validation: {validation.summary()}"
                ),
                generated_sql=generated,
                classification=classification,
                validation=validation,
            )

        audit.validation_result = "PASS"
        is_write = classification.kind in ("write", "ddl")

        if is_write and is_potentially_unbounded_write(tree):
            audit.approval_decision = "REJECTED_UNBOUNDED"
            return self._unbounded_response(
                request_id, generated, classification, validation, 0
            )

        if not execute:
            return AgentResponse(
                status="success",
                request_id=request_id,
                message=f"SQL is valid ({classification.operation}); not executed.",
                generated_sql=generated,
                classification=classification,
                validation=validation,
                approval_required=is_write,
            )

        if classification.kind == "read":
            return self._execute_read(
                request_id, audit, generated, tree, classification, validation, 0
            )

        if not approved:
            audit.approval_decision = "PENDING"
            return AgentResponse(
                status="pending_approval",
                request_id=request_id,
                message="This statement changes data. Review it, then run it or cancel.",
                generated_sql=generated,
                classification=classification,
                validation=validation,
                approval_required=True,
            )

        return self._handle_write(
            request_id,
            audit,
            generated,
            tree,
            classification,
            validation,
            schema,
            0,
            provider=_ApproveOnce(),
        )

    def _process(
        self, request: str, request_id: str, audit: AuditRecord, execute: bool
    ) -> AgentResponse:
        schema = self.schema_manager.get_schema()
        llm_schema = self._llm_schema(schema)

        generated: GeneratedSQL | None = None
        error_context: str | None = None
        attempts = 0

        for attempt in range(self.max_repair_attempts + 1):
            attempts = attempt + 1
            try:
                generated = self.llm.generate_sql(
                    request, llm_schema, error_context=error_context
                )
            except LLMError as exc:
                if attempt >= self.max_repair_attempts:
                    audit.error = str(exc)
                    return AgentResponse(
                        status="error",
                        request_id=request_id,
                        message=f"SQL generation failed: {exc}",
                        generated_sql=generated,
                        attempts=attempts,
                    )
                error_context = (
                    f"Your previous answer could not be used: {exc}. "
                    "Answer with the JSON object only."
                )
                continue

            audit.sql = generated.query
            audit.parameters = generated.parameters

            if not generated.query.strip():
                audit.error = "Model declined to generate SQL"
                return AgentResponse(
                    status="rejected",
                    request_id=request_id,
                    message=(
                        "I couldn't turn that into a database query. "
                        + (generated.explanation or "")
                    ).strip(),
                    generated_sql=generated,
                    attempts=attempts,
                )

            tree, classification, validation, generated = self._validate_generated(
                generated, schema
            )
            audit.parameters = generated.parameters
            if validation.valid:
                break

            audit.validation_result = validation.summary()
            if validation.stage == "policy":
                # Forbidden by policy (DDL disabled, dangerous function, ...).
                # Asking the LLM again would only burn attempts.
                audit.error = validation.summary()
                return AgentResponse(
                    status="rejected",
                    request_id=request_id,
                    message=f"Rejected by security policy: {classification.reason or validation.summary()}",
                    generated_sql=generated,
                    classification=classification,
                    validation=validation,
                    attempts=attempts,
                )
            if attempt >= self.max_repair_attempts:
                audit.error = validation.summary()
                return AgentResponse(
                    status="validation_failed",
                    request_id=request_id,
                    message=(
                        "The generated SQL could not be validated after "
                        f"{attempts} attempt(s): {validation.summary()}"
                    ),
                    generated_sql=generated,
                    classification=classification,
                    validation=validation,
                    attempts=attempts,
                )
            error_context = self._repair_context(generated, validation)

        assert generated is not None
        audit.operation_type = classification.operation
        audit.tables = classification.tables
        audit.validation_result = "PASS"

        if classification.kind in ("write", "ddl") and is_potentially_unbounded_write(tree):
            audit.approval_decision = "REJECTED_UNBOUNDED"
            return self._unbounded_response(
                request_id, generated, classification, validation, attempts
            )

        if not execute:
            note = (
                " Executing it would require human approval."
                if classification.kind in ("write", "ddl")
                else ""
            )
            return AgentResponse(
                status="success",
                request_id=request_id,
                message=f"SQL generated and validated ({classification.operation}); not executed.{note}",
                generated_sql=generated,
                classification=classification,
                validation=validation,
                approval_required=classification.kind in ("write", "ddl"),
                attempts=attempts,
            )

        if classification.kind == "read":
            return self._execute_read(
                request_id, audit, generated, tree, classification, validation, attempts
            )
        return self._handle_write(
            request_id, audit, generated, tree, classification, validation, schema, attempts
        )

    # ------------------------------------------------------------------ read

    def _execute_read(
        self,
        request_id: str,
        audit: AuditRecord,
        generated: GeneratedSQL,
        tree: exp.Expression,
        classification: ClassificationResult,
        validation: ValidationResult,
        attempts: int,
    ) -> AgentResponse:
        execution = self.executor.execute_read(
            generated.query,
            generated.parameters,
            stream=isinstance(tree, READ_ROOTS),
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
                attempts=attempts,
            )
        return AgentResponse(
            status="success",
            request_id=request_id,
            message=(
                "Query executed successfully. "
                f"{execution.row_count} row(s) returned"
                + (" (truncated)." if execution.truncated else ".")
            ),
            generated_sql=generated,
            classification=classification,
            validation=validation,
            execution=execution,
            executed=True,
            attempts=attempts,
        )

    # ----------------------------------------------------------------- write

    def _handle_write(
        self,
        request_id: str,
        audit: AuditRecord,
        generated: GeneratedSQL,
        tree: exp.Expression,
        classification: ClassificationResult,
        validation: ValidationResult,
        schema: dict,
        attempts: int,
        provider: ApprovalProvider | None = None,
    ) -> AgentResponse:
        provider = provider or self.approval_provider
        if provider is None:
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
                attempts=attempts,
            )

        while True:
            # Re-checked on EVERY loop: an edited statement must satisfy the
            # same policy as the generated one (previously an edit could turn
            # the query into "DELETE FROM customers" and it was executed).
            if is_potentially_unbounded_write(tree):
                audit.approval_decision = "REJECTED_UNBOUNDED"
                return self._unbounded_response(
                    request_id, generated, classification, validation, attempts
                )

            approval_request = ApprovalRequest(
                request_id=request_id,
                operation=classification.operation,
                sql=generated.query,
                parameters=generated.parameters,
                explanation=generated.explanation,
                tables=classification.tables,
            )
            decision = provider.request(approval_request)

            if decision.edited_sql:
                edited = GeneratedSQL(
                    query=decision.edited_sql,
                    parameters=(
                        decision.edited_parameters
                        if decision.edited_parameters is not None
                        else {}
                    ),
                    explanation="User-edited SQL",
                )
                tree, classification, validation, generated = self._validate_generated(
                    edited, schema
                )
                audit.sql = generated.query
                audit.parameters = generated.parameters
                audit.operation_type = classification.operation
                audit.tables = classification.tables
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
                        attempts=attempts,
                    )
                if classification.kind not in ("write", "ddl"):
                    audit.approval_decision = "EDITED_NOT_A_WRITE"
                    return AgentResponse(
                        status="rejected",
                        request_id=request_id,
                        message=(
                            "The edited SQL is not a write statement. "
                            "Submit read queries as a normal request instead."
                        ),
                        generated_sql=generated,
                        classification=classification,
                        validation=validation,
                        attempts=attempts,
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
                    attempts=attempts,
                )

            audit.approval_decision = "APPROVED"
            token = self.approval_manager.create_token(approval_request)
            execution = self.executor.execute_write(
                generated.query,
                generated.parameters,
                token.token,
                kind=classification.kind,
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
                    approval_required=True,
                    attempts=attempts,
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
                approval_required=True,
                executed=True,
                attempts=attempts,
            )

    # ------------------------------------------------------------ validation

    def _validate_generated(self, generated: GeneratedSQL, schema: dict) -> Validated:
        """parse -> classify (policy) -> parameters -> schema -> live EXPLAIN.

        Returns the (possibly cleaned) GeneratedSQL as the 4th element:
        parameters that the SQL does not use are dropped.
        """
        try:
            tree = self.parser.parse_single(generated.query)
        except SQLParseError as exc:
            classification = ClassificationResult(
                kind="unsupported", operation="PARSE_ERROR", reason=str(exc)
            )
            validation = ValidationResult(valid=False, stage="parser")
            validation.add("parse_error", str(exc))
            return exp.Command(this="PARSE_ERROR"), classification, validation, generated

        classification = self.classifier.classify(tree)
        if classification.kind == "unsupported":
            validation = ValidationResult(valid=False, stage="policy")
            validation.add("unsupported", classification.reason or "Unsupported statement")
            return tree, classification, validation, generated

        param_result, cleaned = check_parameters(tree, generated.query, generated.parameters)
        if cleaned != generated.parameters:
            generated = generated.model_copy(update={"parameters": cleaned})
        if not param_result.valid:
            return tree, classification, param_result, generated

        validation = self.schema_validator.validate(tree, classification, schema)
        if not validation.valid:
            return tree, classification, validation, generated

        if self.live_validator is not None:
            live_result = self.live_validator.validate(
                tree, generated.query, generated.parameters, classification
            )
            if not live_result.valid:
                return tree, classification, live_result, generated

        return tree, classification, validation, generated

    # --------------------------------------------------------------- helpers

    def _llm_schema(self, schema: dict) -> dict:
        """Prefer the rich metadata (PK/FK) for the prompt when available."""
        get_metadata = getattr(self.schema_manager, "get_metadata", None)
        if callable(get_metadata):
            try:
                metadata = get_metadata()
                if isinstance(metadata, dict) and metadata:
                    return metadata
            except Exception:
                pass
        return schema

    @staticmethod
    def _repair_context(generated: GeneratedSQL, validation: ValidationResult) -> str:
        return (
            f"SQL: {generated.query}\n"
            f"Parameters: {json.dumps(generated.parameters, default=str)}\n"
            f"Problems: {validation.summary()}"
        )

    @staticmethod
    def _unbounded_response(
        request_id: str,
        generated: GeneratedSQL,
        classification: ClassificationResult,
        validation: ValidationResult,
        attempts: int,
    ) -> AgentResponse:
        return AgentResponse(
            status="rejected",
            request_id=request_id,
            message=(
                "UPDATE/DELETE without a WHERE clause on a real column "
                "is rejected by policy."
            ),
            generated_sql=generated,
            classification=classification,
            validation=validation,
            attempts=attempts,
        )
