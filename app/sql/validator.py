from __future__ import annotations

from sqlglot import exp

from app.models import ClassificationResult, ValidationResult


class SQLValidator:
    def validate_structure(
        self,
        tree: exp.Expression,
        classification: ClassificationResult,
    ) -> ValidationResult:
        result = ValidationResult(stage="structure")
        if classification.kind == "unsupported":
            result.add(
                "unsupported_statement",
                classification.reason or "Unsupported statement type",
            )
        return result
