from __future__ import annotations

from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlglot import exp

from app.models import ClassificationResult, ValidationResult


class LiveValidator:
    def __init__(self, engine: Engine, statement_timeout_ms: int = 5000) -> None:
        self.engine = engine
        self.statement_timeout_ms = statement_timeout_ms

    def validate(
        self,
        tree: exp.Expression,
        sql: str,
        parameters: dict,
        classification: ClassificationResult,
    ) -> ValidationResult:
        result = ValidationResult(stage="live")
        if classification.kind != "read":
            return result
        if isinstance(tree, exp.Command):
            return result

        explain_sql = f"EXPLAIN {sql}"
        try:
            with self.engine.connect() as conn:
                with conn.begin():
                    conn.execute(
                        text("SELECT set_config('statement_timeout', :ms, true)"),
                        {"ms": str(self.statement_timeout_ms)},
                    )
                    conn.execute(text("SET TRANSACTION READ ONLY"))
                    conn.execute(text(explain_sql), parameters)
        except (DBAPIError, SQLAlchemyError) as exc:
            message = str(getattr(exc, "orig", exc)).splitlines()[0]
            result.add("live_validation", f"Database planning failed: {message}")
        except Exception as exc:
            result.add("live_validation", f"Live validation error: {exc}")
        return result
