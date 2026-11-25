from __future__ import annotations
 
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlglot import exp
 
from app.database.errors import db_error_message
from app.models import ClassificationResult, ValidationResult
 
 
class LiveValidator:
    """Best-effort planning probe with EXPLAIN (never EXPLAIN ANALYZE).
 
    * Reads are planned on the read-only role.
    * Writes are optionally planned on the writer role (it has the needed
      privileges) inside a READ ONLY transaction that is always rolled back.
      Plain EXPLAIN does not execute the statement, so nothing is changed,
      but wrong columns / types / missing params are caught BEFORE the human
      is asked to approve, and the LLM gets a chance to repair them.
    """
 
    def __init__(
        self,
        engine: Engine,
        statement_timeout_ms: int = 5000,
        writer_engine: Engine | None = None,
    ) -> None:
        self.engine = engine
        self.writer_engine = writer_engine
        self.statement_timeout_ms = statement_timeout_ms
 
    def validate(
        self,
        tree: exp.Expression,
        sql: str,
        parameters: dict,
        classification: ClassificationResult,
    ) -> ValidationResult:
        result = ValidationResult(stage="live")
        if isinstance(tree, exp.Command):
            return result
        if classification.kind == "read":
            engine = self.engine
        elif classification.kind == "write" and self.writer_engine is not None:
            engine = self.writer_engine
        else:
            return result
 
        explain_sql = "EXPLAIN " + sql.strip().rstrip(";")
        try:
            with engine.connect() as conn:
                transaction = conn.begin()
                try:
                    conn.execute(text("SET TRANSACTION READ ONLY"))
                    conn.execute(
                        text("SELECT set_config('statement_timeout', :ms, true)"),
                        {"ms": str(self.statement_timeout_ms)},
                    )
                    conn.execute(text(explain_sql), parameters)
                finally:
                    transaction.rollback()
        except SQLAlchemyError as exc:
            message = db_error_message(exc)
            if classification.kind == "write" and "read-only transaction" in message:
                return result  # server refused to plan the write; skip the probe
            result.add("live_validation", f"Database planning failed: {message}")
        except Exception as exc:
            result.add("live_validation", f"Live validation error: {exc}")
        return result
