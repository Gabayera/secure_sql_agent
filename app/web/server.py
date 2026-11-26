"""HTTP API + single-page UI for the Secure SQL Agent.

Security model
--------------
* All validation and execution go through ``SQLAgent`` exactly as in the CLI.
* A write is never executed from SQL sent by the browser at approval time.
  The server keeps every pending write itself; the browser can only say
  "approve <id>" or "reject <id>". The stored SQL is validated again and then
  executed through the normal one-time-token write path.
* The server binds to 127.0.0.1 by default and has no login. Do not expose it
  on a network without putting authentication in front of it.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.engine import make_url

from app.config import Settings
from app.models import AgentResponse, GeneratedSQL
from app.services.query_service import QueryService

STATIC_DIR = Path(__file__).parent / "static"
PENDING_TTL_SECONDS = 15 * 60
PREVIEW_ROWS = 50


# ----------------------------------------------------------------- bodies


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    mode: Literal["run", "generate"] = "run"


class SQLBody(BaseModel):
    sql: str = Field(min_length=1, max_length=20000)
    parameters: dict[str, Any] = Field(default_factory=dict)
    mode: Literal["run", "validate"] = "run"
    replaces: str | None = None  # pending id this edit replaces
    question: str | None = None


class PendingBody(BaseModel):
    pending_id: str


class ModelBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)


# ---------------------------------------------------------- pending writes


@dataclass
class PendingWrite:
    generated: GeneratedSQL
    question: str | None
    created: float


class PendingStore:
    """Writes waiting for a decision. Kept on the server, never in the browser."""

    def __init__(self, ttl: float = PENDING_TTL_SECONDS) -> None:
        self.ttl = ttl
        self._items: dict[str, PendingWrite] = {}
        self._lock = threading.Lock()

    def add(self, generated: GeneratedSQL, question: str | None) -> str:
        pending_id = uuid.uuid4().hex
        with self._lock:
            self._purge()
            self._items[pending_id] = PendingWrite(generated, question, time.monotonic())
        return pending_id

    def pop(self, pending_id: str) -> PendingWrite | None:
        with self._lock:
            self._purge()
            return self._items.pop(pending_id, None)

    def _purge(self) -> None:
        now = time.monotonic()
        for key in [k for k, v in self._items.items() if now - v.created > self.ttl]:
            del self._items[key]


# -------------------------------------------------------------------- app


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _out(response: AgentResponse, pending_id: str | None = None) -> dict[str, Any]:
    data = response.model_dump(mode="json")
    data["pending_id"] = pending_id
    return data


def create_app(service: QueryService, settings: Settings) -> FastAPI:
    agent = service.agent
    pending = PendingStore()
    app = FastAPI(title="Secure SQL Agent", docs_url="/api/docs", redoc_url=None)

    try:
        database_name = make_url(settings.readonly_database_url).database or ""
    except Exception:
        database_name = ""

    def current_model() -> str | None:
        return getattr(agent.llm, "model", None)

    def maybe_pending(response: AgentResponse, question: str | None) -> dict[str, Any]:
        if response.status == "pending_approval" and response.generated_sql is not None:
            return _out(response, pending.add(response.generated_sql, question))
        return _out(response)

    # ----------------------------------------------------------- pages

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    # ---------------------------------------------------------- status

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return {
            "provider": settings.llm_provider,
            "model": current_model(),
            "database": database_name,
            "dialect": settings.database_dialect,
            "schemas": settings.allowed_schemas,
            "ddl_enabled": settings.enable_ddl,
            "max_write_rows": settings.max_write_rows,
            "max_result_rows": settings.max_result_rows,
            "live_validate_writes": settings.live_validate_writes,
        }

    # ---------------------------------------------------------- schema

    @app.get("/api/schema")
    def schema() -> dict[str, Any]:
        metadata = agent.schema_manager.get_metadata()
        tables: list[dict[str, Any]] = []
        for db in sorted(metadata):
            for name in sorted(metadata[db]):
                info = metadata[db][name]
                pk = set(info.get("primary_key") or [])
                fk_by_col: dict[str, dict[str, str]] = {}
                for fk in info.get("foreign_keys") or []:
                    for col, ref_col in zip(fk.get("columns") or [], fk.get("referred_columns") or []):
                        fk_by_col[col] = {
                            "schema": fk.get("referred_schema") or db,
                            "table": fk.get("referred_table") or "",
                            "column": ref_col,
                        }
                tables.append(
                    {
                        "schema": db,
                        "name": name,
                        "kind": info.get("kind", "table"),
                        "columns": [
                            {
                                "name": col,
                                "type": ctype,
                                "pk": col in pk,
                                "fk": fk_by_col.get(col),
                            }
                            for col, ctype in (info.get("columns") or {}).items()
                        ],
                        "row_count": None,
                    }
                )
        _add_row_counts(tables)
        return {"tables": tables}

    def _add_row_counts(tables: list[dict[str, Any]]) -> None:
        if not tables or len(tables) > 200:
            return
        parts = [
            f"SELECT {i} AS idx, count(*) AS n FROM {_quote(t['schema'])}.{_quote(t['name'])}"
            for i, t in enumerate(tables)
        ]
        try:
            result = agent.executor.execute_read(" UNION ALL ".join(parts), {})
        except Exception:
            return
        if not result.success or not result.rows:
            return
        for row in result.rows:
            idx = row.get("idx")
            if isinstance(idx, int) and 0 <= idx < len(tables):
                tables[idx]["row_count"] = row.get("n")

    @app.post("/api/schema/refresh")
    def refresh_schema() -> dict[str, Any]:
        agent.schema_manager.refresh()
        return schema()

    @app.get("/api/tables/{schema_name}/{table_name}/preview")
    def preview(schema_name: str, table_name: str) -> dict[str, Any]:
        metadata = agent.schema_manager.get_metadata()
        if table_name not in metadata.get(schema_name, {}):
            raise HTTPException(404, f"Unknown table {schema_name}.{table_name}")
        sql = f"SELECT * FROM {_quote(schema_name)}.{_quote(table_name)} LIMIT {PREVIEW_ROWS}"
        result = agent.executor.execute_read(sql, {})
        if not result.success:
            raise HTTPException(400, result.error or "Preview failed")
        return {"sql": sql, **result.model_dump(mode="json")}

    # --------------------------------------------------------- questions

    @app.post("/api/ask")
    def ask(body: AskBody) -> dict[str, Any]:
        question = body.question.strip()
        planned = service.generate(question)
        if (
            body.mode == "generate"
            or planned.status != "success"
            or planned.generated_sql is None
            or planned.classification is None
        ):
            return _out(planned)

        if planned.classification.kind == "read":
            result = agent.execute_sql(planned.generated_sql, question=question)
            result.attempts = planned.attempts
            if result.generated_sql is not None and planned.generated_sql.explanation:
                result.generated_sql.explanation = planned.generated_sql.explanation
            return _out(result)

        planned.status = "pending_approval"
        planned.message = "This changes data. Review it, then run it or cancel."
        return maybe_pending(planned, question)

    @app.post("/api/sql")
    def run_sql(body: SQLBody) -> dict[str, Any]:
        if body.replaces:
            pending.pop(body.replaces)
        generated = GeneratedSQL(
            query=body.sql.strip(),
            parameters=body.parameters,
            explanation="Written by hand" if body.question is None else "Edited by hand",
        )
        result = agent.execute_sql(
            generated, question=body.question, execute=body.mode == "run"
        )
        return maybe_pending(result, body.question)

    @app.post("/api/approve")
    def approve(body: PendingBody) -> dict[str, Any]:
        item = pending.pop(body.pending_id)
        if item is None:
            raise HTTPException(404, "This change has expired or was already handled. Ask again.")
        result = agent.execute_sql(item.generated, question=item.question, approved=True)
        return _out(result)

    @app.post("/api/reject")
    def reject(body: PendingBody) -> dict[str, Any]:
        item = pending.pop(body.pending_id)
        if item is not None:
            agent.record_rejection(item.generated, question=item.question)
        return {"ok": True}

    # ------------------------------------------------------------ models

    @app.get("/api/models")
    def models() -> dict[str, Any]:
        available: list[str] = []
        error: str | None = None
        if settings.llm_provider == "ollama":
            try:
                resp = httpx.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags", timeout=5)
                resp.raise_for_status()
                available = sorted(m["name"] for m in resp.json().get("models", []))
            except Exception as exc:
                error = f"Cannot list Ollama models: {exc}"
        current = current_model()
        if current and current not in available:
            available.insert(0, current)
        return {
            "provider": settings.llm_provider,
            "current": current,
            "available": available,
            "switchable": settings.llm_provider in ("ollama", "openai"),
            "error": error,
        }

    @app.post("/api/models")
    def set_model(body: ModelBody) -> dict[str, Any]:
        if not hasattr(agent.llm, "model"):
            raise HTTPException(400, "The current provider has no selectable model.")
        agent.llm.model = body.name.strip()
        return models()

    return app
