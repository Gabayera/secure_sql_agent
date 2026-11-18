from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    llm_provider: Literal["mock", "openai", "ollama"] = "mock"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:7b"

    database_dialect: str = "postgres"
    readonly_database_url: str = ""
    writer_database_url: str = ""

    allowed_schemas: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["public"]
    )
    default_schema: str = "public"
    schema_cache_ttl: int = 60

    enable_ddl: bool = False
    max_write_rows: int = 100

    max_result_rows: int = 1000
    statement_timeout_ms: int = 5000
    lock_timeout_ms: int = 2000

    max_sql_repair_attempts: int = 2

    audit_log_path: str = "logs/audit.log"

    @field_validator("allowed_schemas", mode="before")
    @classmethod
    def _parse_allowed_schemas(cls, value):
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return ["public"]
            if raw.startswith("["):
                return json.loads(raw)
            return [item.strip() for item in raw.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        if not self.readonly_database_url:
            raise ValueError("READONLY_DATABASE_URL is required")
        if not self.writer_database_url:
            raise ValueError("WRITER_DATABASE_URL is required")
        if self.readonly_database_url == self.writer_database_url:
            raise ValueError(
                "READONLY_DATABASE_URL and WRITER_DATABASE_URL must use different database roles"
            )
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        if self.default_schema not in self.allowed_schemas:
            self.allowed_schemas = [*self.allowed_schemas, self.default_schema]
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
