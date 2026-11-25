from __future__ import annotations
 
import re
 
from sqlglot import exp
 
from app.models import ClassificationResult
 
WRITE_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Merge)
 
# exp.Alter was called exp.AlterTable in some sqlglot versions.
DDL_NODES = tuple(
    node
    for node in (
        getattr(exp, "Create", None),
        getattr(exp, "Drop", None),
        getattr(exp, "Alter", None),
        getattr(exp, "AlterTable", None),
        getattr(exp, "TruncateTable", None),
    )
    if node is not None
)
 
SET_OPERATIONS = tuple(
    node
    for node in (
        getattr(exp, "Union", None),
        getattr(exp, "Intersect", None),
        getattr(exp, "Except", None),
    )
    if node is not None
)
 
READ_ROOTS = (exp.Select, *SET_OPERATIONS)
 
DEFAULT_DANGEROUS_FUNCTIONS: set[str] = {
    # file system / large objects
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "pg_ls_logdir", "pg_ls_waldir", "pg_ls_tmpdir", "pg_ls_archive_statusdir",
    "pg_file_write", "lo_import", "lo_export", "lo_get", "lo_put",
    "lo_create", "lo_creat", "lo_unlink", "lo_from_bytea",
    # remote execution / arbitrary SQL in a string
    "dblink", "dblink_exec", "dblink_connect", "dblink_open", "dblink_send_query",
    "query_to_xml", "query_to_xmlschema", "query_to_xml_and_xmlschema",
    "cursor_to_xml", "table_to_xml", "schema_to_xml", "database_to_xml",
    "database_to_xml_and_xmlschema",
    # denial of service / locking
    "pg_sleep", "pg_sleep_for", "pg_sleep_until",
    "pg_advisory_lock", "pg_advisory_xact_lock", "pg_advisory_lock_shared",
    "pg_try_advisory_lock", "pg_try_advisory_xact_lock",
    # server administration
    "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf",
    "pg_rotate_logfile", "pg_switch_wal", "pg_create_restore_point",
    "pg_start_backup", "pg_stop_backup", "pg_backup_start", "pg_backup_stop",
    "pg_promote", "pg_wal_replay_pause", "pg_wal_replay_resume",
    "pg_log_backend_memory_contexts", "pg_export_snapshot",
    "pg_notify", "pg_logical_emit_message",
    # session configuration
    "set_config", "current_setting",
}
 
_ANALYZE = re.compile(r"\bANALY[SZ]E\b", re.IGNORECASE)  # Postgres accepts both spellings
 
 
def extract_table_names(tree: exp.Expression) -> list[str]:
    names: set[str] = set()
    for table in tree.find_all(exp.Table):
        if isinstance(table.this, exp.Func):
            continue  # table function such as generate_series(...)
        parts = [p for p in (table.catalog, table.db, table.name) if p]
        names.add(".".join(parts) if parts else table.name)
    return sorted(names)
 
 
def _iter_function_names(tree: exp.Expression):
    for node in tree.walk():
        if isinstance(node, exp.Anonymous):
            yield node.name.lower()
        elif isinstance(node, exp.Func):
            name = ""
            try:
                name = node.sql_name() or ""
            except Exception:
                name = ""
            if not name:
                name = type(node).__name__
            yield name.lower()
 
 
class SQLClassifier:
    def __init__(
        self,
        enable_ddl: bool = False,
        dangerous_functions: set[str] | None = None,
        dialect: str = "postgres",
    ) -> None:
        self.enable_ddl = enable_ddl
        self.dangerous_functions = (
            dangerous_functions
            if dangerous_functions is not None
            else set(DEFAULT_DANGEROUS_FUNCTIONS)
        )
        self.dialect = dialect
 
    def classify(self, tree: exp.Expression) -> ClassificationResult:
        tables = extract_table_names(tree)
 
        if isinstance(tree, exp.Command):
            return self._classify_command(tree, tables)
 
        # Dangerous functions are forbidden in EVERY statement type,
        # not only in SELECTs (previously UPDATE ... SET x = pg_sleep(..) passed).
        dangerous = sorted(set(_iter_function_names(tree)) & self.dangerous_functions)
        if dangerous:
            return ClassificationResult(
                kind="unsupported",
                operation="DANGEROUS_FUNCTION",
                tables=tables,
                reason=f"Dangerous function(s) not allowed: {', '.join(dangerous)}",
            )
 
        if isinstance(tree, READ_ROOTS):
            return self._classify_read(tree, tables)
 
        if isinstance(tree, WRITE_NODES):
            # A write may not hide another write in a CTE / sub-query,
            # e.g. WITH d AS (DELETE FROM users RETURNING id) INSERT ...
            for node in tree.walk():
                if node is not tree and isinstance(node, WRITE_NODES):
                    return ClassificationResult(
                        kind="unsupported",
                        operation="NESTED_WRITE",
                        tables=tables,
                        reason="A write statement may not contain another data-modifying statement",
                    )
            return ClassificationResult(
                kind="write", operation=type(tree).__name__.upper(), tables=tables
            )
 
        if DDL_NODES and isinstance(tree, DDL_NODES):
            if not self.enable_ddl:
                return ClassificationResult(
                    kind="unsupported",
                    operation=type(tree).__name__.upper(),
                    tables=tables,
                    reason="DDL is disabled by configuration",
                )
            return ClassificationResult(
                kind="ddl", operation=type(tree).__name__.upper(), tables=tables
            )
 
        return ClassificationResult(
            kind="unsupported",
            operation=type(tree).__name__,
            tables=tables,
            reason=f"Unsupported statement type: {type(tree).__name__}",
        )
 
    def _classify_read(self, tree: exp.Expression, tables: list[str]) -> ClassificationResult:
        if tree.find(exp.Into):
            return ClassificationResult(
                kind="unsupported",
                operation="SELECT_INTO",
                tables=tables,
                reason="SELECT INTO is not allowed",
            )
 
        lock_cls = getattr(exp, "Lock", None)
        if lock_cls is not None and tree.find(lock_cls):
            return ClassificationResult(
                kind="unsupported",
                operation="SELECT_FOR_UPDATE",
                tables=tables,
                reason="SELECT FOR UPDATE / FOR SHARE is not allowed",
            )
 
        for node in tree.walk():
            if isinstance(node, WRITE_NODES):
                return ClassificationResult(
                    kind="unsupported",
                    operation="DATA_MODIFYING_CTE",
                    tables=tables,
                    reason="Data-modifying CTEs / nested writes are not allowed",
                )
 
        return ClassificationResult(
            kind="read", operation="SELECT", is_safe_read=True, tables=tables
        )
 
    def _classify_command(self, tree: exp.Command, tables: list[str]) -> ClassificationResult:
        raw = tree.sql(dialect=self.dialect).strip().upper()
 
        if raw.startswith("EXPLAIN"):
            if _ANALYZE.search(raw):
                return ClassificationResult(
                    kind="unsupported",
                    operation="EXPLAIN_ANALYZE",
                    tables=tables,
                    reason="EXPLAIN ANALYZE executes the query and is not allowed",
                )
            return ClassificationResult(
                kind="read", operation="EXPLAIN", is_safe_read=True, tables=tables
            )
 
        if raw.startswith(("SHOW", "DESCRIBE", "DESC")):
            return ClassificationResult(
                kind="read", operation="METADATA", is_safe_read=True, tables=tables
            )
 
        return ClassificationResult(
            kind="unsupported",
            operation="COMMAND",
            tables=tables,
            reason=f"Unsupported command: {raw.split()[0] if raw else 'UNKNOWN'}",
        )
