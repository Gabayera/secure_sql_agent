from __future__ import annotations
 
import argparse
import sys
 
from app.agent import SQLAgent
from app.config import Settings, get_settings
from app.database.connection import create_readonly_engine, create_writer_engine
from app.database.executor import DatabaseExecutor
from app.database.introspection import SchemaIntrospector
from app.database.schema_manager import SchemaManager
from app.llm.base import LLMClient
from app.llm.mock_client import MockLLMClient
from app.llm.ollama_client import OllamaLLMClient
from app.llm.openai_client import OpenAILLMClient
from app.llm.prompts import PromptOptions, render_schema
from app.models import AgentResponse
from app.security.approval import ApprovalManager, CLIApprovalProvider
from app.security.audit import AuditLogger
from app.security.permissions import PermissionChecker
from app.services.query_service import QueryService
from app.sql.classifier import SQLClassifier
from app.sql.live_validator import LiveValidator
from app.sql.parser import SQLParser
from app.sql.schema_validator import SchemaValidator
 
MAX_DISPLAY_ROWS = 50
MAX_CELL_WIDTH = 40
 
 
def build_llm(settings: Settings) -> LLMClient:
    options = PromptOptions(
        dialect=settings.database_dialect,
        default_schema=settings.default_schema,
        max_result_rows=settings.max_result_rows,
        enable_ddl=settings.enable_ddl,
    )
    if settings.llm_provider == "openai":
        assert settings.openai_api_key is not None
        return OpenAILLMClient(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            base_url=settings.openai_base_url or None,
            timeout=settings.llm_timeout_seconds,
            options=options,
        )
    if settings.llm_provider == "ollama":
        return OllamaLLMClient(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout=settings.llm_timeout_seconds,
            num_ctx=settings.ollama_num_ctx,
            options=options,
        )
    return MockLLMClient()
 
 
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
        readonly_engine,
        statement_timeout_ms=settings.statement_timeout_ms,
        writer_engine=writer_engine if settings.live_validate_writes else None,
    )
 
    agent = SQLAgent(
        llm=build_llm(settings),
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
 
 
# ---------------------------------------------------------------- output
 
 
def _print_banner(settings: Settings, service: QueryService | None) -> None:
    model = {
        "openai": settings.openai_model,
        "ollama": settings.ollama_model,
    }.get(settings.llm_provider, "-")
    print("=" * 50)
    print("              SECURE SQL AI AGENT")
    print("=" * 50)
    print(f"Database: {settings.database_dialect}")
    print(f"Status:   {'Connected' if service else 'Unavailable'}")
    print(f"Schema:   {settings.default_schema}")
    print(f"LLM:      {settings.llm_provider} ({model})")
    print("=" * 50)
    print("Type a question in plain language, 'sql <question>' to only")
    print("generate SQL without running it, or 'help'.")
 
 
def _cell(value) -> str:
    text = "NULL" if value is None else str(value).replace("\n", " ")
    return text if len(text) <= MAX_CELL_WIDTH else text[: MAX_CELL_WIDTH - 1] + "…"
 
 
def format_table(columns: list[str], rows: list[dict]) -> str:
    if not rows:
        return "(no rows)"
    columns = columns or list(rows[0].keys())
    data = [[_cell(row.get(col)) for col in columns] for row in rows]
    widths = [
        max(len(str(col)), *(len(r[i]) for r in data)) for i, col in enumerate(columns)
    ]
    sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    head = "| " + " | ".join(str(c).ljust(w) for c, w in zip(columns, widths)) + " |"
    body = [
        "| " + " | ".join(v.ljust(w) for v, w in zip(r, widths)) + " |" for r in data
    ]
    return "\n".join([sep, head, sep, *body, sep])
 
 
def _print_response(response: AgentResponse) -> None:
    print()
    if response.generated_sql and response.generated_sql.query:
        print("Generated SQL:\n")
        print(response.generated_sql.query.rstrip().rstrip(";") + ";")
        if response.generated_sql.parameters:
            print("\nParameters:")
            for key, value in response.generated_sql.parameters.items():
                print(f"  {key} = {value!r}")
        if response.generated_sql.explanation:
            print(f"\nExplanation: {response.generated_sql.explanation}")
        print()
    if response.validation:
        status = "[PASS]" if response.validation.valid else "[FAIL]"
        print(f"Validation: {status} {response.validation.stage}")
        if not response.validation.valid:
            for issue in response.validation.issues:
                print(f"  - {issue.code}: {issue.message}")
        print()
    if response.status == "success" and response.execution:
        execution = response.execution
        if execution.rows is not None:
            shown = execution.rows[:MAX_DISPLAY_ROWS]
            print(format_table(execution.columns, shown))
            hidden = execution.row_count - len(shown)
            if hidden > 0:
                print(f"... {hidden} more row(s) not shown")
            if execution.truncated:
                print("(Result truncated at MAX_RESULT_ROWS)")
        elif execution.affected_rows is not None:
            print(f"Affected rows: {execution.affected_rows}")
        print()
    print(f"Agent: {response.message}")
    print("-" * 50)
 
 
HELP = """
Commands:
  <question>        Generate SQL, validate it and run it
                    (reads run automatically, writes ask for approval)
  sql <question>    Only generate + validate the SQL, do not run it
  refresh_schema    Reload schema from the database
  show_schema       Print the schema the LLM sees
  show_status       Show connection / LLM status
  help              Show this help
  exit / quit       Exit
"""
 
 
def _repl(settings: Settings, service: QueryService | None) -> int:
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
            print(HELP)
            continue
        if service is None:
            print("Agent: database is not initialised. Check the startup error and restart.")
            continue
        agent = service.agent
        try:
            if command == "refresh_schema":
                agent.schema_manager.refresh()
                print("Schema refreshed.")
                continue
            if command == "show_schema":
                print(render_schema(agent.schema_manager.get_metadata()))
                continue
            if command == "show_status":
                print(f"LLM provider: {settings.llm_provider}")
                print(f"Dialect:      {settings.database_dialect}")
                print(f"Schemas:      {', '.join(settings.allowed_schemas)}")
                print(f"DDL enabled:  {settings.enable_ddl}")
                print(f"Live-check writes: {settings.live_validate_writes}")
                continue
            if command.startswith("sql "):
                _print_response(service.generate(raw[4:].strip()))
                continue
            _print_response(service.execute(raw))
        except KeyboardInterrupt:
            print("\n(cancelled)")
        except Exception as exc:
            print(f"Agent: error: {exc}")
 
 
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Secure natural-language SQL agent")
    parser.add_argument("question", nargs="*", help="run one question and exit")
    parser.add_argument(
        "--sql-only", action="store_true", help="only generate + validate SQL, never execute"
    )
    parser.add_argument("--json", action="store_true", help="print the response as JSON")
    args = parser.parse_args(argv)
 
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
 
    question = " ".join(args.question).strip()
    if question:
        if service is None:
            return 1
        response = service.generate(question) if args.sql_only else service.execute(question)
        if args.json:
            print(response.model_dump_json(indent=2))
        else:
            _print_response(response)
        return 0 if response.status == "success" else 2
 
    _print_banner(settings, service)
    return _repl(settings, service)
 
 
if __name__ == "__main__":
    raise SystemExit(main())
