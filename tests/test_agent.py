from __future__ import annotations

from unittest.mock import MagicMock

from app.agent import SQLAgent
from app.llm.mock_client import MockLLMClient
from app.models import ApprovalDecision, ExecutionResult
from app.security.approval import ApprovalManager
from app.sql.classifier import SQLClassifier
from app.sql.parser import SQLParser
from app.sql.schema_validator import SchemaValidator

SCHEMA = {
    "public": {
        "orders": {
            "id": "INTEGER",
            "customer_id": "INTEGER",
            "amount": "DECIMAL",
            "created_at": "TIMESTAMP",
        },
        "products": {"id": "INTEGER", "name": "VARCHAR", "price": "DECIMAL"},
        "customers": {"id": "INTEGER", "name": "VARCHAR", "email": "VARCHAR"},
    }
}


class _SchemaManager:
    def get_schema(self, force=False):
        return SCHEMA


class _LiveValidator:
    def validate(self, *args, **kwargs):
        from app.models import ValidationResult

        return ValidationResult(valid=True, stage="live")


class _YesProvider:
    def request(self, request):
        return ApprovalDecision(approved=True)


def _build_agent(executor, provider=None):
    manager = ApprovalManager(provider or _YesProvider())
    return SQLAgent(
        llm=MockLLMClient(),
        schema_manager=_SchemaManager(),
        parser=SQLParser(dialect="postgres"),
        classifier=SQLClassifier(dialect="postgres"),
        schema_validator=SchemaValidator(dialect="postgres", default_schema="public"),
        live_validator=_LiveValidator(),
        executor=executor,
        approval_manager=manager,
        audit=None,
        max_repair_attempts=2,
        approval_provider=provider or _YesProvider(),
    )


def test_read_executes_automatically():
    executor = MagicMock()
    executor.execute_read.return_value = ExecutionResult(
        success=True, rows=[{"id": 1}], row_count=1
    )
    agent = _build_agent(executor)
    response = agent.process_request("Show me the latest 10 orders")
    assert response.status == "success"
    executor.execute_read.assert_called_once()


def test_write_requires_approval():
    executor = MagicMock()
    executor.execute_write.return_value = ExecutionResult(
        success=True, affected_rows=1, row_count=0
    )
    agent = _build_agent(executor)
    response = agent.process_request("Change the price of product 15 to 250")
    assert response.status == "success"
    executor.execute_write.assert_called_once()


def test_rejected_write_does_not_execute():
    class NoProvider:
        def request(self, request):
            return ApprovalDecision(approved=False)

    executor = MagicMock()
    agent = _build_agent(executor, provider=NoProvider())
    response = agent.process_request("Change the price of product 15 to 250")
    assert response.status == "rejected"
    executor.execute_write.assert_not_called()


def test_invalid_sql_stops_after_repair_limit():
    class BadLLM:
        def generate_sql(self, request, schema, error_context=None):
            from app.models import GeneratedSQL

            return GeneratedSQL(
                query="SELECT missing_column FROM missing_table",
                parameters={},
                explanation="bad",
            )

    executor = MagicMock()
    manager = ApprovalManager(_YesProvider())
    agent = SQLAgent(
        llm=BadLLM(),
        schema_manager=_SchemaManager(),
        parser=SQLParser(dialect="postgres"),
        classifier=SQLClassifier(dialect="postgres"),
        schema_validator=SchemaValidator(dialect="postgres", default_schema="public"),
        live_validator=_LiveValidator(),
        executor=executor,
        approval_manager=manager,
        audit=None,
        max_repair_attempts=1,
        approval_provider=_YesProvider(),
    )
    response = agent.process_request("anything")
    assert response.status == "validation_failed"
    executor.execute_read.assert_not_called()
    executor.execute_write.assert_not_called()
