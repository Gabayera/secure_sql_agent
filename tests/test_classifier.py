from __future__ import annotations

from app.sql.classifier import SQLClassifier


def _classify(parser, sql, enable_ddl=False):
    tree = parser.parse_single(sql)
    return SQLClassifier(enable_ddl=enable_ddl, dialect="postgres").classify(tree)


def test_safe_select(parser):
    c = _classify(parser, "SELECT id FROM users")
    assert c.kind == "read" and c.is_safe_read is True


def test_select_into_rejected(parser):
    c = _classify(parser, "SELECT * INTO backup FROM users")
    assert c.kind == "unsupported" and "SELECT INTO" in (c.reason or "")


def test_select_for_update_rejected(parser):
    assert _classify(parser, "SELECT id FROM users FOR UPDATE").kind == "unsupported"


def test_data_modifying_cte_rejected(parser):
    sql = "WITH deleted AS (DELETE FROM users RETURNING id) SELECT * FROM deleted"
    assert _classify(parser, sql).kind == "unsupported"


def test_dangerous_function_rejected(parser):
    assert _classify(parser, "SELECT pg_read_file('/etc/passwd')").kind == "unsupported"


def test_explain_analyze_rejected(parser):
    assert _classify(parser, "EXPLAIN ANALYZE SELECT id FROM users").kind == "unsupported"


def test_ddl_rejected_when_disabled(parser):
    assert _classify(parser, "DROP TABLE users", enable_ddl=False).kind == "unsupported"


def test_unknown_command_rejected(parser):
    assert _classify(parser, "CALL some_procedure()").kind == "unsupported"
