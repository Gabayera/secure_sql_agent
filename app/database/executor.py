from __future__ import annotations
 
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError
 
from app.database.errors import db_error_message
from app.models import ExecutionResult
from app.security.approval import ApprovalError, ApprovalManager
 
 
class ExecutionError(RuntimeError):
    pass
 
 
class DatabaseExecutor:
    def __init__(
        self,
        readonly_engine: Engine,
        writer_engine: Engine,
        approval_manager: ApprovalManager,
        max_result_rows: int = 1000,
        max_write_rows: int = 100,
        statement_timeout_ms: int = 5000,
        lock_timeout_ms: int = 2000,
    ) -> None:
        self.readonly_engine = readonly_engine
        self.writer_engine = writer_engine
        self.approval_manager = approval_manager
        self.max_result_rows = max_result_rows
        self.max_write_rows = max_write_rows
        self.statement_timeout_ms = statement_timeout_ms
        self.lock_timeout_ms = lock_timeout_ms
 
    def execute_read(
        self, sql: str, parameters: dict, stream: bool = False
    ) -> ExecutionResult:
        """Run a read query on the read-only role.
 
        ``stream=True`` uses a server-side cursor so a huge result set is not
        pulled into memory just to keep ``max_result_rows`` of it. Only valid
        for plain SELECT / set operations (not EXPLAIN / SHOW).
        """
        options = {"stream_results": True} if stream else {}
        try:
            with self.readonly_engine.connect() as conn:
                with conn.begin():
                    # Must be the first statement of the transaction.
                    conn.execute(text("SET TRANSACTION READ ONLY"))
                    conn.execute(
                        text("SELECT set_config('statement_timeout', :ms, true)"),
                        {"ms": str(self.statement_timeout_ms)},
                    )
                    result = conn.execute(
                        text(sql), parameters, execution_options=options
                    )
                    columns = list(result.keys())
                    rows = result.mappings().fetchmany(self.max_result_rows + 1)
                    truncated = len(rows) > self.max_result_rows
                    rows = rows[: self.max_result_rows]
                    result.close()
                    return ExecutionResult(
                        success=True,
                        columns=columns,
                        rows=[dict(row) for row in rows],
                        row_count=len(rows),
                        truncated=truncated,
                    )
        except SQLAlchemyError as exc:
            return ExecutionResult(success=False, error=db_error_message(exc))
 
    def execute_write(
        self,
        sql: str,
        parameters: dict,
        approval_token: str,
        kind: str = "write",
    ) -> ExecutionResult:
        try:
            self.approval_manager.verify_and_consume(approval_token, sql, parameters)
        except ApprovalError as exc:
            return ExecutionResult(success=False, error=f"Approval check failed: {exc}")
 
        try:
            with self.writer_engine.connect() as conn:
                transaction = conn.begin()
                try:
                    conn.execute(
                        text("SELECT set_config('statement_timeout', :ms, true)"),
                        {"ms": str(self.statement_timeout_ms)},
                    )
                    conn.execute(
                        text("SELECT set_config('lock_timeout', :ms, true)"),
                        {"ms": str(self.lock_timeout_ms)},
                    )
                    result = conn.execute(text(sql), parameters)
                    affected = result.rowcount
 
                    if kind == "ddl":
                        # DDL has no meaningful row count.
                        transaction.commit()
                        return ExecutionResult(success=True, affected_rows=0)
 
                    if affected is None or affected < 0:
                        transaction.rollback()
                        return ExecutionResult(
                            success=False,
                            error=(
                                "Affected-row count could not be determined reliably; "
                                "transaction rolled back."
                            ),
                        )
                    if affected > self.max_write_rows:
                        transaction.rollback()
                        return ExecutionResult(
                            success=False,
                            error=(
                                f"Affected rows ({affected}) exceed the configured limit "
                                f"({self.max_write_rows}); transaction rolled back."
                            ),
                        )
 
                    transaction.commit()
                    return ExecutionResult(
                        success=True, affected_rows=affected, row_count=0
                    )
                except Exception:
                    if transaction.is_active:
                        transaction.rollback()
                    raise
        except SQLAlchemyError as exc:
            return ExecutionResult(success=False, error=db_error_message(exc))
        except Exception as exc:
            return ExecutionResult(success=False, error=str(exc))
