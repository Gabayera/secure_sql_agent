from __future__ import annotations

import time
from typing import Any

from sqlglot.schema import MappingSchema

from app.database.introspection import SchemaIntrospector


class SchemaManager:
    def __init__(
        self,
        introspector: SchemaIntrospector,
        dialect: str = "postgres",
        cache_ttl: int = 60,
    ) -> None:
        self.introspector = introspector
        self.dialect = dialect
        self.cache_ttl = cache_ttl
        self._schema: dict[str, dict[str, dict[str, str]]] | None = None
        self._metadata: dict[str, dict[str, Any]] | None = None
        self._cached_at: float = 0.0

    def get_schema(self, force: bool = False) -> dict[str, dict[str, dict[str, str]]]:
        self._ensure_cache(force=force)
        assert self._schema is not None
        return self._schema

    def get_metadata(self, force: bool = False) -> dict[str, dict[str, Any]]:
        self._ensure_cache(force=force)
        assert self._metadata is not None
        return self._metadata

    def refresh(self) -> None:
        self._schema = self.introspector.introspect()
        self._metadata = self.introspector.introspect_metadata()
        self._cached_at = time.monotonic()

    def get_mapping_schema(self) -> MappingSchema:
        return MappingSchema(self.get_schema(), dialect=self.dialect)

    def get_table_columns(self, schema: str, table: str) -> dict[str, str]:
        return self.get_schema().get(schema, {}).get(table, {})

    def _ensure_cache(self, force: bool = False) -> None:
        expired = (time.monotonic() - self._cached_at) > self.cache_ttl
        if force or self._schema is None or self._metadata is None or expired:
            self.refresh()
