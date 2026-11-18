from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, inspect


class SchemaIntrospector:
    def __init__(
        self,
        engine: Engine,
        allowed_schemas: list[str],
        default_schema: str = "public",
    ) -> None:
        self.engine = engine
        self.allowed_schemas = set(allowed_schemas)
        self.default_schema = default_schema

    def introspect(self) -> dict[str, dict[str, dict[str, str]]]:
        inspector = inspect(self.engine)
        result: dict[str, dict[str, dict[str, str]]] = {}

        for schema in inspector.get_schema_names():
            if schema not in self.allowed_schemas:
                continue
            result[schema] = {}

            for table_name in inspector.get_table_names(schema=schema):
                result[schema][table_name] = self._columns(inspector, schema, table_name)

            for view_name in inspector.get_view_names(schema=schema):
                if view_name not in result[schema]:
                    result[schema][view_name] = self._columns(inspector, schema, view_name)

        if self.default_schema not in result:
            result[self.default_schema] = {}
        return result

    def introspect_metadata(self) -> dict[str, dict[str, Any]]:
        inspector = inspect(self.engine)
        metadata: dict[str, dict[str, Any]] = {}

        for schema in inspector.get_schema_names():
            if schema not in self.allowed_schemas:
                continue
            metadata[schema] = {}

            for table_name in inspector.get_table_names(schema=schema):
                metadata[schema][table_name] = self._table_metadata(
                    inspector, schema, table_name, "table"
                )

            for view_name in inspector.get_view_names(schema=schema):
                if view_name not in metadata[schema]:
                    metadata[schema][view_name] = self._table_metadata(
                        inspector, schema, view_name, "view"
                    )

        return metadata

    @staticmethod
    def _columns(inspector, schema: str, table: str) -> dict[str, str]:
        return {
            col["name"]: str(col.get("type", ""))
            for col in inspector.get_columns(table, schema=schema)
        }

    def _table_metadata(self, inspector, schema: str, table: str, kind: str) -> dict[str, Any]:
        columns = self._columns(inspector, schema, table)
        try:
            pk = inspector.get_pk_constraint(table, schema=schema)
            primary_key = pk.get("constrained_columns", [])
        except Exception:
            primary_key = []
        try:
            foreign_keys = inspector.get_foreign_keys(table, schema=schema)
        except Exception:
            foreign_keys = []
        return {
            "kind": kind,
            "columns": columns,
            "primary_key": primary_key,
            "foreign_keys": foreign_keys,
        }
