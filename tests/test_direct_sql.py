"""SQLAgent.execute_sql: the entry point used by the web UI."""
from __future__ import annotations

from unittest.mock import MagicMock

from app.agent import SQLAgent
from app.llm.mock_client import MockLLMClient
from app.models import ExecutionResult, GeneratedSQL, ValidationResult
from app.security.approval import ApprovalManager
from app.sql.classifier import SQLClassifier
from app.sql.parser import SQLParser
from app.sql.schema_validator import SchemaValidator

from tests.conftest import SCHEMA


class _SchemaManager:
    def get_schema(self, force=False):
        return SCHEMA


class _LiveOK:
    def validate(self, *args, **kwargs):
        return ValidationResult(valid=True, stage="live")


def _agent(executor):
    return SQLAgent(
        llm=MockLLMClient(),
        schema_manager=_SchemaManager(),
        parser=SQLParser(dialect="postgres"),
        classifier=SQLClassifier(dialect="postgres"),
        schema_validator=SchemaValidator(dialect="postgres", default_schema="public"),
        live_validator=_LiveOK(),
        executor=executor,
        approval_manager=ApprovalManager(None),
        audit=None,
        approval_provider=None,
    )


def _executor():
    executor = MagicMock()
    executor.execute_read.return_value = ExecutionResult(
        success=True, columns=["id"], rows=[{"id": 1}], row_count=1
    )
    executor.execute_write.return_value = ExecutionResult(success=True, affected_rows=1)
    return executor


UPDATE = GeneratedSQL(
    query="UPDATE products SET price = :p_price WHERE id = :p_id",
    parameters={"p_price": 39, "p_id": 7},
)


def test_read_runs():
    executor = _executor()
    response = _agent(executor).execute_sql(GeneratedSQL(query="SELECT id, name FROM users"))
    assert response.status == "success" and response.executed
    executor.execute_read.assert_called_once()


def test_validate_only_does_not_run():
    executor = _executor()
    response = _agent(executor).execute_sql(
        GeneratedSQL(query="SELECT id FROM users"), execute=False
    )
    assert response.status == "success" and not response.executed
    executor.execute_read.assert_not_called()


def test_write_without_approval_is_pending():
    executor = _executor()
    response = _agent(executor).execute_sql(UPDATE)
    assert response.status == "pending_approval"
    executor.execute_write.assert_not_called()


def test_write_with_approval_runs_once_with_token():
    executor = _executor()
    response = _agent(executor).execute_sql(UPDATE, approved=True)
    assert response.status == "success" and response.executed
    executor.execute_write.assert_called_once()
    args, kwargs = executor.execute_write.call_args
    assert args[0] == UPDATE.query and args[1] == UPDATE.parameters
    assert isinstance(args[2], str) and len(args[2]) > 20  # one-time token
    assert kwargs.get("kind") == "write"


def test_unbounded_write_rejected_even_if_approved():
    executor = _executor()
    response = _agent(executor).execute_sql(
        GeneratedSQL(query="DELETE FROM customers"), approved=True
    )
    assert response.status == "rejected"
    executor.execute_write.assert_not_called()


def test_policy_violation_rejected():
    executor = _executor()
    response = _agent(executor).execute_sql(
        GeneratedSQL(query="DROP TABLE orders"), approved=True
    )
    assert response.status == "rejected"
    executor.execute_write.assert_not_called()


def test_unknown_column_is_validation_failed():
    executor = _executor()
    response = _agent(executor).execute_sql(GeneratedSQL(query="SELECT age FROM users"))
    assert response.status == "validation_failed"
    executor.execute_read.assert_not_called()


def test_mock_fallback_declines_instead_of_select_1():
    response = _agent(_executor()).process_request("hi")
    assert response.status == "rejected"
    assert response.generated_sql is not None and response.generated_sql.query == ""
