"""Regression tests for the bugs fixed in v0.2.0."""
from __future__ import annotations
 
from unittest.mock import MagicMock
 
from app.agent import SQLAgent
from app.llm.base import parse_llm_payload
from app.llm.mock_client import MockLLMClient
from app.models import ApprovalDecision, GeneratedSQL, ValidationResult
from app.security.approval import ApprovalManager
from app.sql.classifier import SQLClassifier
from app.sql.params import check_parameters
from app.sql.parser import SQLParser
from app.sql.safety import is_potentially_unbounded_write
from app.sql.schema_validator import SchemaValidator
 
from tests.conftest import SCHEMA
 
 
def _classify(parser, sql):
    return SQLClassifier(dialect="postgres").classify(parser.parse_single(sql))
 
 
def _validate(parser, sql):
    tree = parser.parse_single(sql)
    c = SQLClassifier(dialect="postgres").classify(tree)
    return SchemaValidator(dialect="postgres", default_schema="public").validate(tree, c, SCHEMA)
 
 
# ---------------------------------------------------------------- classifier
 
def test_union_is_a_read(parser):
    c = _classify(parser, "SELECT id FROM users UNION SELECT id FROM customers")
    assert c.kind == "read"
 
 
def test_dangerous_function_blocked_in_write(parser):
    c = _classify(parser, "UPDATE users SET name = pg_read_file('/etc/passwd') WHERE id = 1")
    assert c.kind == "unsupported"
 
 
def test_nested_write_inside_write_blocked(parser):
    sql = (
        "WITH d AS (DELETE FROM users RETURNING id) "
        "INSERT INTO orders (user_id) SELECT id FROM d"
    )
    assert _classify(parser, sql).kind == "unsupported"
 
 
def test_explain_analyse_british_spelling_blocked(parser):
    assert _classify(parser, "EXPLAIN ANALYSE SELECT id FROM users").kind == "unsupported"
 
 
# -------------------------------------------------------------------- safety
 
def test_where_only_in_subquery_is_unbounded(parser):
    tree = parser.parse_single(
        "UPDATE products SET price = (SELECT MAX(price) FROM products WHERE id = 1)"
    )
    assert is_potentially_unbounded_write(tree)
 
 
def test_constant_where_is_unbounded(parser):
    assert is_potentially_unbounded_write(parser.parse_single("DELETE FROM users WHERE 1 = 1"))
 
 
def test_real_where_is_bounded(parser):
    assert not is_potentially_unbounded_write(
        parser.parse_single("DELETE FROM users WHERE id = :id")
    )
 
 
# ------------------------------------------------------------ schema checks
 
def test_insert_unknown_column_rejected(parser):
    assert not _validate(parser, "INSERT INTO users (nope) VALUES (:v)").valid
 
 
def test_insert_known_columns_accepted(parser):
    assert _validate(parser, "INSERT INTO users (name, email) VALUES (:n, :e)").valid
 
 
def test_identifiers_are_case_insensitive(parser):
    assert _validate(parser, "SELECT ID FROM Users").valid
 
 
# ---------------------------------------------------------------- parameters
 
def test_missing_parameter_detected(parser):
    tree = parser.parse_single("SELECT id FROM users WHERE id = :p_id")
    result, _ = check_parameters(tree, "SELECT id FROM users WHERE id = :p_id", {})
    assert not result.valid
 
 
def test_bind_with_pg_cast_detected(parser):
    sql = "SELECT id FROM users WHERE created_at > CAST(:p_d AS DATE)"
    tree = parser.parse_single(sql)
    ok, _ = check_parameters(tree, sql, {"p_d": "2024-01-01"})
    assert ok.valid
    bad_sql = "SELECT id FROM users WHERE created_at > :p_d::date"
    bad, _ = check_parameters(parser.parse_single(bad_sql), bad_sql, {"p_d": "2024-01-01"})
    assert not bad.valid
 
 
def test_unused_parameters_are_dropped(parser):
    sql = "SELECT id FROM users WHERE id = :p_id"
    result, cleaned = check_parameters(parser.parse_single(sql), sql, {"p_id": 1, "extra": 2})
    assert result.valid and cleaned == {"p_id": 1}
 
 
# -------------------------------------------------------------- LLM parsing
 
def test_parse_payload_tolerates_fences_and_aliases():
    content = '```json\n{"sql": "SELECT 1", "params": [{"name": ":p_x", "value": 3}]}\n```'
    g = parse_llm_payload(content)
    assert g.query == "SELECT 1" and g.parameters == {"p_x": 3}
 
 
# --------------------------------------------------------------------- agent
 
class _SchemaManager:
    def get_schema(self, force=False):
        return SCHEMA
 
 
class _LiveOK:
    def validate(self, *args, **kwargs):
        return ValidationResult(valid=True, stage="live")
 
 
def _agent(llm, executor, provider):
    return SQLAgent(
        llm=llm,
        schema_manager=_SchemaManager(),
        parser=SQLParser(dialect="postgres"),
        classifier=SQLClassifier(dialect="postgres"),
        schema_validator=SchemaValidator(dialect="postgres", default_schema="public"),
        live_validator=_LiveOK(),
        executor=executor,
        approval_manager=ApprovalManager(provider),
        audit=None,
        max_repair_attempts=2,
        approval_provider=provider,
    )
 
 
def test_edited_sql_without_where_is_never_executed():
    class EditThenApprove:
        calls = 0
 
        def request(self, request):
            self.calls += 1
            if self.calls == 1:
                return ApprovalDecision(edited_sql="DELETE FROM customers", edited_parameters={})
            return ApprovalDecision(approved=True)
 
    executor = MagicMock()
    response = _agent(MockLLMClient(), executor, EditThenApprove()).process_request(
        "delete customer 3"
    )
    assert response.status == "rejected"
    executor.execute_write.assert_not_called()
 
 
def test_policy_violation_is_not_retried():
    class DropLLM:
        calls = 0
 
        def generate_sql(self, request, schema, error_context=None):
            self.calls += 1
            return GeneratedSQL(query="DROP TABLE orders")
 
    llm = DropLLM()
    executor = MagicMock()
    response = _agent(llm, executor, None).process_request("drop the orders table")
    assert response.status == "rejected"
    assert llm.calls == 1
 
 
def test_generate_only_never_executes():
    executor = MagicMock()
    response = _agent(MockLLMClient(), executor, None).generate_only(
        "Change the price of product 15 to 250"
    )
    assert response.status == "success" and response.executed is False
    executor.execute_write.assert_not_called()
    executor.execute_read.assert_not_called()
 
 
def test_empty_query_means_cannot_answer():
    class DeclineLLM:
        def generate_sql(self, request, schema, error_context=None):
            return GeneratedSQL(query="", explanation="No weather table exists.")
 
    response = _agent(DeclineLLM(), MagicMock(), None).process_request("weather tomorrow?")
    assert response.status == "rejected"
    assert "weather" in response.message
