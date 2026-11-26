"""HTTP API of the web UI, tested with a fake agent (no database, no LLM)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from app.models import (  # noqa: E402
    AgentResponse,
    ClassificationResult,
    ExecutionResult,
    GeneratedSQL,
    ValidationResult,
)
from app.web.server import create_app  # noqa: E402

METADATA = {
    "public": {
        "customers": {
            "kind": "table",
            "columns": {"id": "INTEGER", "full_name": "VARCHAR(150)"},
            "primary_key": ["id"],
            "foreign_keys": [],
        },
        "orders": {
            "kind": "table",
            "columns": {"id": "INTEGER", "customer_id": "INTEGER"},
            "primary_key": ["id"],
            "foreign_keys": [
                {
                    "columns": ["customer_id"],
                    "referred_schema": "public",
                    "referred_table": "customers",
                    "referred_columns": ["id"],
                }
            ],
        },
    }
}


def _cls(kind: str) -> ClassificationResult:
    return ClassificationResult(kind=kind, operation="UPDATE" if kind == "write" else "SELECT", tables=["orders"])


class FakeAgent:
    def __init__(self) -> None:
        self.llm = SimpleNamespace(model="model-a")
        self.schema_manager = SimpleNamespace(get_metadata=lambda: METADATA, refresh=lambda: None)
        self.executor = SimpleNamespace(execute_read=self._read)
        self.calls: list[dict] = []
        self.rejected: list[GeneratedSQL] = []

    def _read(self, sql, params, stream=False):
        if "count(*)" in sql:
            return ExecutionResult(success=True, columns=["idx", "n"], rows=[{"idx": 0, "n": 10}, {"idx": 1, "n": 12}], row_count=2)
        return ExecutionResult(success=True, columns=["id"], rows=[{"id": 1}], row_count=1)

    def execute_sql(self, generated, user=None, question=None, approved=False, execute=True):
        self.calls.append({"sql": generated.query, "approved": approved, "execute": execute, "question": question})
        is_write = generated.query.upper().startswith("UPDATE")
        if not execute:
            return AgentResponse(status="success", request_id="x", message="valid", generated_sql=generated, classification=_cls("write" if is_write else "read"))
        if is_write and not approved:
            return AgentResponse(status="pending_approval", request_id="x", message="needs approval", generated_sql=generated, classification=_cls("write"))
        if is_write:
            return AgentResponse(status="success", request_id="x", message="done", generated_sql=generated, classification=_cls("write"), execution=ExecutionResult(success=True, affected_rows=1), executed=True)
        return AgentResponse(status="success", request_id="x", message="ok", generated_sql=generated, classification=_cls("read"), validation=ValidationResult(), execution=ExecutionResult(success=True, columns=["id"], rows=[{"id": 1}], row_count=1), executed=True)

    def record_rejection(self, generated, user=None, question=None):
        self.rejected.append(generated)


class FakeService:
    def __init__(self) -> None:
        self.agent = FakeAgent()

    def generate(self, question, user=None):
        if "price" in question:
            g = GeneratedSQL(query="UPDATE products SET price = :p WHERE id = :i", parameters={"p": 1, "i": 2}, explanation="update")
            return AgentResponse(status="success", request_id="g", message="planned", generated_sql=g, classification=_cls("write"))
        g = GeneratedSQL(query="SELECT id FROM orders", explanation="list orders")
        return AgentResponse(status="success", request_id="g", message="planned", generated_sql=g, classification=_cls("read"), attempts=2)


SETTINGS = SimpleNamespace(
    llm_provider="mock",
    readonly_database_url="postgresql+psycopg://ro:pw@localhost:5433/sqlagent_demo",
    database_dialect="postgres",
    allowed_schemas=["public"],
    enable_ddl=False,
    max_write_rows=100,
    max_result_rows=1000,
    live_validate_writes=True,
    ollama_base_url="http://localhost:11434",
)


@pytest.fixture()
def client_and_agent():
    service = FakeService()
    return TestClient(create_app(service, SETTINGS)), service.agent


def test_index_served(client_and_agent):
    client, _ = client_and_agent
    resp = client.get("/")
    assert resp.status_code == 200 and "SQL Agent" in resp.text


def test_status(client_and_agent):
    client, _ = client_and_agent
    data = client.get("/api/status").json()
    assert data["database"] == "sqlagent_demo" and data["model"] == "model-a"


def test_schema_has_keys_and_counts(client_and_agent):
    client, _ = client_and_agent
    tables = {t["name"]: t for t in client.get("/api/schema").json()["tables"]}
    assert tables["customers"]["row_count"] == 10
    cols = {c["name"]: c for c in tables["orders"]["columns"]}
    assert cols["id"]["pk"] is True
    assert cols["customer_id"]["fk"] == {"schema": "public", "table": "customers", "column": "id"}


def test_preview(client_and_agent):
    client, _ = client_and_agent
    assert client.get("/api/tables/public/orders/preview").status_code == 200
    assert client.get("/api/tables/public/nope/preview").status_code == 404


def test_ask_read_runs_and_keeps_explanation(client_and_agent):
    client, agent = client_and_agent
    data = client.post("/api/ask", json={"question": "list orders"}).json()
    assert data["status"] == "success" and data["executed"] is True
    assert data["attempts"] == 2
    assert data["generated_sql"]["explanation"] == "list orders"
    assert agent.calls == [{"sql": "SELECT id FROM orders", "approved": False, "execute": True, "question": "list orders"}]


def test_ask_generate_only_never_executes(client_and_agent):
    client, agent = client_and_agent
    data = client.post("/api/ask", json={"question": "list orders", "mode": "generate"}).json()
    assert data["status"] == "success"
    assert agent.calls == []


def test_write_needs_approval_and_token_is_single_use(client_and_agent):
    client, agent = client_and_agent
    data = client.post("/api/ask", json={"question": "set price"}).json()
    assert data["status"] == "pending_approval" and data["pending_id"]
    assert agent.calls == []  # nothing executed yet

    ok = client.post("/api/approve", json={"pending_id": data["pending_id"]})
    assert ok.status_code == 200 and ok.json()["executed"] is True
    assert agent.calls[-1]["approved"] is True
    assert agent.calls[-1]["sql"].startswith("UPDATE products")

    again = client.post("/api/approve", json={"pending_id": data["pending_id"]})
    assert again.status_code == 404


def test_reject_records_and_blocks_later_approval(client_and_agent):
    client, agent = client_and_agent
    pid = client.post("/api/ask", json={"question": "set price"}).json()["pending_id"]
    assert client.post("/api/reject", json={"pending_id": pid}).status_code == 200
    assert len(agent.rejected) == 1
    assert client.post("/api/approve", json={"pending_id": pid}).status_code == 404


def test_edit_replaces_pending(client_and_agent):
    client, _ = client_and_agent
    old = client.post("/api/ask", json={"question": "set price"}).json()["pending_id"]
    new = client.post(
        "/api/sql",
        json={"sql": "UPDATE products SET price = 5 WHERE id = 2", "mode": "run", "replaces": old, "question": "set price"},
    ).json()
    assert new["status"] == "pending_approval" and new["pending_id"] != old
    assert client.post("/api/approve", json={"pending_id": old}).status_code == 404
    assert client.post("/api/approve", json={"pending_id": new["pending_id"]}).status_code == 200


def test_switch_model(client_and_agent):
    client, agent = client_and_agent
    assert client.post("/api/models", json={"name": "model-b"}).status_code == 200
    assert agent.llm.model == "model-b"
