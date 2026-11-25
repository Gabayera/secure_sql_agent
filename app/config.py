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
 
    # LLM
    llm_provider: Literal["mock", "openai", "ollama"] = "mock"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str | None = None  # for OpenAI-compatible servers
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:7b"
    ollama_num_ctx: int = Field(default=8192, ge=1024)
    llm_timeout_seconds: float = Field(default=120.0, gt=0)
 
    # Database
    database_dialect: str = "postgres"
    readonly_database_url: str = ""
    writer_database_url: str = ""
 
    # Schema
    allowed_schemas: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["public"]
    )
    default_schema: str = "public"
    schema_cache_ttl: int = Field(default=60, ge=0)
 
    # Security
    enable_ddl: bool = False
    max_write_rows: int = Field(default=100, ge=1)
    live_validate_writes: bool = True
 
    # Execution
    max_result_rows: int = Field(default=1000, ge=1)
    statement_timeout_ms: int = Field(default=5000, ge=100)
    lock_timeout_ms: int = Field(default=2000, ge=100)
 
    # Agent
    max_sql_repair_attempts: int = Field(default=2, ge=0, le=10)
 
    # Audit
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
