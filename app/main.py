from __future__ import annotations

import json
import sys

from app.agent import SQLAgent
from app.config import Settings, get_settings
from app.database.connection import create_readonly_engine, create_writer_engine
from app.database.executor import DatabaseExecutor
from app.database.introspection import SchemaIntrospector
from app.database.schema_manager import SchemaManager
from app.llm.mock_client import MockLLMClient
from app.llm.ollama_client import OllamaLLMClient
from app.llm.openai_client import OpenAILLMClient
from app.security.approval import ApprovalManager, CLIApprovalProvider
from app.security.audit import AuditLogger
from app.security.permissions import PermissionChecker
from app.services.query_service import QueryService
from app.sql.classifier import SQLClassifier
from app.sql.live_validator import LiveValidator
from app.sql.parser import SQLParser
from app.sql.schema_validator import SchemaValidator


def build_service(settings: Settings) -> QueryService:
    readonly_engine = create_readonly_engine(settings.readonly_database_url)
    writer_engine = create_writer_engine(settings.writer_database_url)
    PermissionChecker(readonly_engine, writer_engine).verify_roles_differ()

    introspector = SchemaIntrospector(
        readonly_engine,
        allowed_schemas=settings.allowed_schemas,
        default_schema=settings.default_schema,
    )
    schema_manager = SchemaManager(
        introspector,
        dialect=settings.database_dialect,
        cache_ttl=settings.schema_cache_ttl,
    )
    schema_manager.refresh()

    if settings.llm_provider == "openai":
        assert settings.openai_api_key is not None
        llm = OpenAILLMClient(
            api_key=settings.openai_api_key, model=settings.openai_model
        )
    elif settings.llm_provider == "ollama":
        llm = OllamaLLMClient(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
        )
    else:
        llm = MockLLMClient()

    approval_provider = CLIApprovalProvider()
    approval_manager = ApprovalManager(approval_provider)
    audit = AuditLogger(settings.audit_log_path)

    executor = DatabaseExecutor(
        readonly_engine=readonly_engine,
        writer_engine=writer_engine,
        approval_manager=approval_manager,
        max_result_rows=settings.max_result_rows,
        max_write_rows=settings.max_write_rows,
        statement_timeout_ms=settings.statement_timeout_ms,
        lock_timeout_ms=settings.lock_timeout_ms,
    )

    live_validator = LiveValidator(
        readonly_engine, statement_timeout_ms=settings.statement_timeout_ms
    )

    agent = SQLAgent(
        llm=llm,
        schema_manager=schema_manager,
        parser=SQLParser(dialect=settings.database_dialect),
        classifier=SQLClassifier(
            enable_ddl=settings.enable_ddl, dialect=settings.database_dialect
        ),
        schema_validator=SchemaValidator(
            dialect=settings.database_dialect,
            default_schema=settings.default_schema,
        ),
        live_validator=live_validator,
        executor=executor,
        approval_manager=approval_manager,
        audit=audit,
        max_repair_attempts=settings.max_sql_repair_attempts,
        approval_provider=approval_provider,
    )
    return QueryService(agent)


def _print_banner(settings: Settings, service: QueryService | None) -> None:
    print("=" * 42)
    print("          SECURE SQL AI AGENT")
    print("=" * 42)
    print(f"Database: {settings.database_dialect}")
    print(f"Status:   {'Connected' if service else 'Unavailable'}")
    print(f"Schema:   {settings.default_schema}")
    print(f"LLM:      {settings.llm_provider}")
    print("=" * 42)


def _print_response(response) -> None:
    print()
    if response.generated_sql:
        print("Generated SQL:\n")
        print(response.generated_sql.query.rstrip(";") + ";")
        if response.generated_sql.parameters:
            print("\nParameters:")
            for key, value in response.generated_sql.parameters.items():
                print(f"  {key} = {value!r}")
        print()
    if response.validation:
        status = "[PASS]" if response.validation.valid else "[FAIL]"
        print(f"Validation: {status} {response.validation.stage}")
        if not response.validation.valid:
            for issue in response.validation.issues:
                print(f"  - {issue.code}: {issue.message}")
        print()
    if response.status == "success" and response.execution:
        if response.execution.rows is not None:
            print(f"Result: {response.execution.row_count} row(s) returned.")
            for row in response.execution.rows:
                print(json.dumps(row, default=str, ensure_ascii=False))
            if response.execution.truncated:
                print("(Result truncated)")
        elif response.execution.affected_rows is not None:
            print(f"Affected rows: {response.execution.affected_rows}")
        print()
    print(f"Agent: {response.message}")
    print("-" * 42)


def main() -> int:
    try:
        settings = get_settings()
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    service: QueryService | None = None
    try:
        service = build_service(settings)
    except Exception as exc:
        print(f"Startup error: {exc}", file=sys.stderr)

    _print_banner(settings, service)
    if service is None:
        print("Database connection is unavailable. Only diagnostic commands work.")

    while True:
        try:
            raw = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not raw:
            continue
        command = raw.lower()
        if command in ("exit", "quit"):
            return 0
        if command == "help":
            print(
                "\nCommands:\n"
                "  help            Show this help\n"
                "  exit / quit     Exit\n"
                "  refresh_schema  Reload schema from the database\n"
                "  show_schema     Print the current schema\n"
                "  show_status     Show connection / LLM status\n"
            )
            continue
        if service is None:
            print("Agent: database/LLM is not initialised. Use 'exit' to quit.")
            continue
        agent = service.agent
        if command == "refresh_schema":
            agent.schema_manager.refresh()
            print("Schema refreshed.")
            continue
        if command == "show_schema":
            print(json.dumps(agent.schema_manager.get_schema(), indent=2))
            continue
        if command == "show_status":
            print(f"LLM provider: {settings.llm_provider}")
            print(f"Dialect:      {settings.database_dialect}")
            print(f"Schemas:      {', '.join(settings.allowed_schemas)}")
            print(f"DDL enabled:  {settings.enable_ddl}")
            continue
        response = service.execute(raw)
        _print_response(response)


if __name__ == "__main__":
    raise SystemExit(main())
