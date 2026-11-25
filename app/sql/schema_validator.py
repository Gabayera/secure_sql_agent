from __future__ import annotations
 
from sqlglot import exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.qualify import qualify
from sqlglot.schema import MappingSchema
 
from app.models import ClassificationResult, ValidationResult
 
Schema = dict[str, dict[str, dict[str, str]]]
 
 
class SchemaValidator:
    def __init__(self, dialect: str = "postgres", default_schema: str = "public") -> None:
        self.dialect = dialect
        self.default_schema = default_schema
 
    def validate(
        self,
        tree: exp.Expression,
        classification: ClassificationResult,
        schema: Schema,
    ) -> ValidationResult:
        if classification.kind == "read":
            return self._validate_read(tree, schema)
        if classification.kind in ("write", "ddl"):
            return self._validate_write(tree, schema)
        result = ValidationResult(valid=False, stage="schema")
        result.add("unsupported", classification.reason or "Unsupported statement")
        return result
 
    # ------------------------------------------------------------------ helpers
 
    def _mapping(self, schema: Schema) -> MappingSchema:
        # Only column NAMES matter for validation. Real DB type strings
        # (enums, domains, arrays...) can fail sqlglot's type parser, so
        # every column is registered as TEXT. Empty schemas are dropped
        # because MappingSchema requires a uniform nesting depth.
        cleaned = {
            db: {table: {col: "TEXT" for col in cols} for table, cols in tables.items() if cols}
            for db, tables in schema.items()
        }
        cleaned = {db: tables for db, tables in cleaned.items() if tables}
        return MappingSchema(cleaned or None, dialect=self.dialect)
 
    def _check_table(
        self, table: exp.Table, schema: Schema, result: ValidationResult
    ) -> tuple[str, str] | None:
        if table.catalog:
            result.add(
                "cross_database",
                f"Cross-database reference '{table.catalog}' is not allowed",
            )
            return None
        db = table.db or self.default_schema
        if db not in schema or table.name not in schema[db]:
            result.add(
                "missing_table",
                f"Table '{db}.{table.name}' does not exist or is not accessible",
            )
            return None
        return db, table.name
 
    # --------------------------------------------------------------------- read
 
    def _validate_read(self, tree: exp.Expression, schema: Schema) -> ValidationResult:
        result = ValidationResult(stage="schema")
 
        if isinstance(tree, exp.Command):
            return result
 
        try:
            # qualify() also normalises identifiers (Postgres folds unquoted
            # names to lower case), so "SELECT ID FROM Users" now validates.
            qualified = qualify(
                tree.copy(),
                dialect=self.dialect,
                schema=self._mapping(schema),
                db=self.default_schema,
                validate_qualify_columns=True,
                expand_stars=True,
                quote_identifiers=False,
                identify=False,
            )
        except (OptimizeError, SqlglotError) as exc:
            message = str(exc).splitlines()[0] if str(exc) else "Schema validation failed"
            result.add("schema_validation", message)
            return result
        except Exception as exc:
            result.add("schema_validation", f"Schema validation error: {exc}")
            return result
 
        cte_names = {cte.alias_or_name for cte in qualified.find_all(exp.CTE) if cte.alias_or_name}
        for table in qualified.find_all(exp.Table):
            if isinstance(table.this, exp.Func):
                continue  # table function, e.g. generate_series(...)
            if not table.db and table.name in cte_names:
                continue
            self._check_table(table, schema, result)
        return result
 
    # -------------------------------------------------------------------- write
 
    def _validate_write(self, tree: exp.Expression, schema: Schema) -> ValidationResult:
        result = ValidationResult(stage="schema")
        tree = normalize_identifiers(tree.copy(), dialect=self.dialect)
 
        cte_names = {cte.alias_or_name for cte in tree.find_all(exp.CTE) if cte.alias_or_name}
        derived_aliases = cte_names | {
            sq.alias for sq in tree.find_all(exp.Subquery) if sq.alias
        }
 
        tables: list[tuple[str, str, str]] = []
        for table in tree.find_all(exp.Table):
            if isinstance(table.this, exp.Func):
                continue
            if not table.db and table.name in cte_names:
                continue
            resolved = self._check_table(table, schema, result)
            if resolved is None:
                continue
            db, name = resolved
            tables.append((db, name, table.alias_or_name or name))
 
        if not tables:
            if result.valid:
                result.add("missing_table", "No target table could be resolved")
            return result
 
        known_aliases = {alias for _, _, alias in tables} | {name for _, name, _ in tables}
 
        # INSERT INTO t (col_a, col_b): these are Identifiers, not Columns,
        # so they were never validated before.
        if isinstance(tree, exp.Insert) and isinstance(tree.this, exp.Schema):
            target = tree.this.this
            if isinstance(target, exp.Table):
                db = target.db or self.default_schema
                target_cols = schema.get(db, {}).get(target.name, {})
                for ident in tree.this.expressions:
                    col_name = ident.name
                    if target_cols and col_name not in target_cols:
                        result.add(
                            "missing_column",
                            f"Column '{col_name}' does not exist in '{db}.{target.name}'",
                        )
 
        has_on_conflict = tree.find(exp.OnConflict) is not None if hasattr(exp, "OnConflict") else False
 
        for column in tree.find_all(exp.Column):
            qualifier = column.table
            if qualifier:
                if has_on_conflict and qualifier.lower() == "excluded":
                    continue
                if qualifier not in known_aliases:
                    if qualifier in derived_aliases:
                        continue  # column of a CTE / sub-query; checked live by EXPLAIN
                    result.add(
                        "unknown_table_reference",
                        f"'{qualifier}.{column.name}' references an unknown table or alias",
                    )
                    continue
            matches = self._resolve_column(column, tables, schema)
            if not matches:
                if not qualifier and derived_aliases:
                    continue  # may come from a CTE / sub-query; checked live
                result.add(
                    "missing_column",
                    f"Column '{column.name}' does not exist in the referenced table(s)",
                )
            elif len(matches) > 1 and not qualifier:
                result.add(
                    "ambiguous_column",
                    f"Column '{column.name}' is ambiguous across {', '.join(matches)}",
                )
        return result
 
    @staticmethod
    def _resolve_column(
        column: exp.Column,
        tables: list[tuple[str, str, str]],
        schema: Schema,
    ) -> list[str]:
        matches: list[str] = []
        for db, table_name, alias in tables:
            if column.table and column.table not in (alias, table_name):
                continue
            columns = schema.get(db, {}).get(table_name, {})
            if column.name in columns:
                matches.append(f"{db}.{table_name}.{column.name}")
        return matches
