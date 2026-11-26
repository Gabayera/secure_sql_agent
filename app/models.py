from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class GeneratedSQL(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    explanation: str = ""


class ValidationIssue(BaseModel):
    code: str
    message: str


class ValidationResult(BaseModel):
    valid: bool = True
    issues: list[ValidationIssue] = Field(default_factory=list)
    stage: str = ""

    def add(self, code: str, message: str) -> None:
        self.valid = False
        self.issues.append(ValidationIssue(code=code, message=message))

    def summary(self) -> str:
        if self.valid:
            return "PASS"
        return "; ".join(f"{i.code}: {i.message}" for i in self.issues)


class ClassificationResult(BaseModel):
    kind: Literal["read", "write", "ddl", "unsupported"]
    operation: str
    is_safe_read: bool = False
    tables: list[str] = Field(default_factory=list)
    reason: str | None = None


class ExecutionResult(BaseModel):
    success: bool
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] | None = None
    row_count: int = 0
    truncated: bool = False
    affected_rows: int | None = None
    error: str | None = None


class ApprovalRequest(BaseModel):
    request_id: str
    operation: str
    sql: str
    parameters: dict[str, Any]
    explanation: str
    tables: list[str]


class ApprovalDecision(BaseModel):
    approved: bool = False
    edited_sql: str | None = None
    edited_parameters: dict[str, Any] | None = None


class AgentResponse(BaseModel):
    status: Literal[
        "success", "error", "rejected", "validation_failed", "pending_approval"
    ]
    request_id: str
    message: str
    generated_sql: GeneratedSQL | None = None
    classification: ClassificationResult | None = None
    validation: ValidationResult | None = None
    execution: ExecutionResult | None = None
    approval_required: bool = False
    executed: bool = False
    attempts: int = 0


class AuditRecord(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    request_id: str
    user: str | None = None
    natural_language_request: str
    mode: str = "execute"
    operation_type: str | None = None
    sql: str | None = None
    tables: list[str] = Field(default_factory=list)
    validation_result: str | None = None
    approval_decision: str | None = None
    execution_success: bool | None = None
    affected_rows: int | None = None
    error: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
