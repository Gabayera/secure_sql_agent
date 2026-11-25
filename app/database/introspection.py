from __future__ import annotations
 
from typing import Any
 
from sqlalchemy import Engine, inspect
 
 
class SchemaIntrospector:
    """Reads tables, views, columns, primary and foreign keys in ONE pass."""
 
    def __init__(
        self,
        engine: Engine,
        allowed_schemas: list[str],
        default_schema: str = "public",
    ) -> None:
        self.engine = engine
        self.allowed_schemas = set(allowed_schemas)
        self.default_schema = default_schema
 
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
 
            view_names = list(inspector.get_view_names(schema=schema))
            get_mviews = getattr(inspector, "get_materialized_view_names", None)
            if get_mviews is not None:
                try:
                    view_names += list(get_mviews(schema=schema))
                except Exception:
                    pass
            for view_name in view_names:
                if view_name not in metadata[schema]:
                    metadata[schema][view_name] = self._table_metadata(
                        inspector, schema, view_name, "view"
                    )
 
        metadata.setdefault(self.default_schema, {})
        return metadata
 
    def introspect(self) -> dict[str, dict[str, dict[str, str]]]:
        return schema_from_metadata(self.introspect_metadata())
 
    @staticmethod
    def _columns(inspector, schema: str, table: str) -> dict[str, str]:
        return {
            col["name"]: str(col.get("type", ""))
            for col in inspector.get_columns(table, schema=schema)
        }
 
    def _table_metadata(
        self, inspector, schema: str, table: str, kind: str
    ) -> dict[str, Any]:
        columns = self._columns(inspector, schema, table)
        primary_key: list[str] = []
        foreign_keys: list[dict[str, Any]] = []
        if kind == "table":
            try:
                pk = inspector.get_pk_constraint(table, schema=schema) or {}
                primary_key = list(pk.get("constrained_columns") or [])
            except Exception:
                primary_key = []
            try:
                for fk in inspector.get_foreign_keys(table, schema=schema):
                    foreign_keys.append(
                        {
                            "columns": list(fk.get("constrained_columns") or []),
                            "referred_schema": fk.get("referred_schema") or schema,
                            "referred_table": fk.get("referred_table"),
                            "referred_columns": list(fk.get("referred_columns") or []),
                        }
                    )
            except Exception:
                foreign_keys = []
        return {
            "kind": kind,
            "columns": columns,
            "primary_key": primary_key,
            "foreign_keys": foreign_keys,
        }
 
 
def schema_from_metadata(
    metadata: dict[str, dict[str, Any]],
) -> dict[str, dict[str, dict[str, str]]]:
    return {
        schema: {table: dict(info.get("columns", {})) for table, info in tables.items()}
        for schema, tables in metadata.items()
    }
