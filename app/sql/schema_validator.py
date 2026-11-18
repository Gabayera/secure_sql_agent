from __future__ import annotations

from sqlglot import exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.schema import MappingSchema

from app.models import ClassificationResult, ValidationResult


class SchemaValidator:
    def __init__(self, dialect: str = "postgres", default_schema: str = "public") -> None:
        self.dialect = dialect
        self.default_schema = default_schema

    def validate(
        self,
        tree: exp.Expression,
        classification: ClassificationResult,
        schema: dict[str, dict[str, dict[str, str]]],
    ) -> ValidationResult:
        if classification.kind == "read":
            return self._validate_read(tree, schema)
        if classification.kind in ("write", "ddl"):
            return self._validate_write(tree, schema)
        result = ValidationResult(valid=False, stage="schema")
        result.add("unsupported", classification.reason or "Unsupported statement")
        return result

    def _validate_read(
        self,
        tree: exp.Expression,
        schema: dict[str, dict[str, dict[str, str]]],
    ) -> ValidationResult:
        result = ValidationResult(stage="schema")

        if isinstance(tree, exp.Command):
            return result

        mapping = MappingSchema(schema, dialect=self.dialect)
        try:
            qualify(
                tree.copy(),
                dialect=self.dialect,
                schema=mapping,
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

        cte_names = {
            cte.alias_or_name
            for cte in tree.find_all(exp.CTE)
            if cte.alias_or_name
        }
        for table in tree.find_all(exp.Table):
            if table.name in cte_names:
                continue
            db = table.db or self.default_schema
            if db not in schema or table.name not in schema[db]:
                result.add(
                    "missing_table",
                    f"Table '{db}.{table.name}' does not exist or is not accessible",
                )
        return result

    def _validate_write(
        self,
        tree: exp.Expression,
        schema: dict[str, dict[str, dict[str, str]]],
    ) -> ValidationResult:
        result = ValidationResult(stage="schema")
        cte_names = {cte.alias_or_name for cte in tree.find_all(exp.CTE) if cte.alias_or_name}

        tables: list[tuple[str, str, str]] = []
        for table in tree.find_all(exp.Table):
            if table.name in cte_names:
                continue
            db = table.db or self.default_schema
            if db not in schema or table.name not in schema[db]:
                result.add(
                    "missing_table",
                    f"Table '{db}.{table.name}' does not exist or is not accessible",
                )
                continue
            tables.append((db, table.name, table.alias_or_name or table.name))

        if not tables:
            result.add("missing_table", "No target table could be resolved")
            return result

        for column in tree.find_all(exp.Column):
            matches = self._resolve_column(column, tables, schema)
            if not matches:
                result.add(
                    "missing_column",
                    f"Column '{column.name}' does not exist in the referenced table(s)",
                )
            elif len(matches) > 1 and not column.table:
                result.add(
                    "ambiguous_column",
                    f"Column '{column.name}' is ambiguous across {', '.join(matches)}",
                )
        return result

    @staticmethod
    def _resolve_column(
        column: exp.Column,
        tables: list[tuple[str, str, str]],
        schema: dict[str, dict[str, dict[str, str]]],
    ) -> list[str]:
        matches: list[str] = []
        for db, table_name, alias in tables:
            if column.table and column.table not in (alias, table_name):
                continue
            columns = schema.get(db, {}).get(table_name, {})
            if column.name in columns:
                matches.append(f"{db}.{table_name}.{column.name}")
        return matches
