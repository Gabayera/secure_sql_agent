from __future__ import annotations

from sqlglot import exp

from app.models import ClassificationResult

WRITE_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Merge)
DDL_NODES = (exp.Create, exp.Drop, exp.Alter, exp.TruncateTable)

DEFAULT_DANGEROUS_FUNCTIONS: set[str] = {
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "lo_import", "lo_export",
    "dblink", "dblink_exec",
    "pg_sleep", "pg_sleep_for", "pg_sleep_until",
    "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf",
    "pg_rotate_logfile", "pg_switch_wal", "pg_create_restore_point",
    "pg_start_backup", "pg_stop_backup", "pg_promote",
    "pg_wal_replay_pause", "pg_wal_replay_resume",
    "set_config",
}


def extract_table_names(tree: exp.Expression) -> list[str]:
    names: set[str] = set()
    for table in tree.find_all(exp.Table):
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

        if isinstance(tree, exp.Select):
            return self._classify_select(tree, tables)

        if isinstance(tree, exp.Command):
            return self._classify_command(tree, tables)

        if isinstance(tree, exp.Insert):
            return ClassificationResult(kind="write", operation="INSERT", tables=tables)
        if isinstance(tree, exp.Update):
            return ClassificationResult(kind="write", operation="UPDATE", tables=tables)
        if isinstance(tree, exp.Delete):
            return ClassificationResult(kind="write", operation="DELETE", tables=tables)
        if isinstance(tree, exp.Merge):
            return ClassificationResult(kind="write", operation="MERGE", tables=tables)

        if isinstance(tree, DDL_NODES):
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

    def _classify_select(self, tree: exp.Select, tables: list[str]) -> ClassificationResult:
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

        dangerous = sorted(set(_iter_function_names(tree)) & self.dangerous_functions)
        if dangerous:
            return ClassificationResult(
                kind="unsupported",
                operation="DANGEROUS_FUNCTION",
                tables=tables,
                reason=f"Dangerous function(s) not allowed: {', '.join(dangerous)}",
            )

        return ClassificationResult(
            kind="read", operation="SELECT", is_safe_read=True, tables=tables
        )

    def _classify_command(self, tree: exp.Command, tables: list[str]) -> ClassificationResult:
        raw = tree.sql(dialect=self.dialect).strip().upper()

        if raw.startswith("EXPLAIN"):
            if "ANALYZE" in raw:
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
