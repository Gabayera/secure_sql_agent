from __future__ import annotations

from app.sql.classifier import SQLClassifier
from app.sql.schema_validator import SchemaValidator


def _validate(parser, sql, schema):
    tree = parser.parse_single(sql)
    c = SQLClassifier(dialect="postgres").classify(tree)
    v = SchemaValidator(dialect="postgres", default_schema="public").validate(tree, c, schema)
    return c, v


def test_existing_table_and_column(parser, schema):
    _, v = _validate(parser, "SELECT id FROM users", schema)
    assert v.valid


def test_missing_table(parser, schema):
    _, v = _validate(parser, "SELECT * FROM missing_table", schema)
    assert not v.valid


def test_missing_column(parser, schema):
    _, v = _validate(parser, "SELECT full_name FROM users", schema)
    assert not v.valid


def test_schema_qualified_table(parser, schema):
    _, v = _validate(parser, "SELECT id FROM public.users", schema)
    assert v.valid


def test_invalid_schema_qualified_table(parser, schema):
    _, v = _validate(parser, "SELECT id FROM other.users", schema)
    assert not v.valid


def test_alias(parser, schema):
    _, v = _validate(parser, "SELECT u.id FROM users u", schema)
    assert v.valid


def test_join(parser, schema):
    sql = "SELECT u.name, o.amount FROM users u JOIN orders o ON u.id = o.user_id"
    _, v = _validate(parser, sql, schema)
    assert v.valid


def test_cte(parser, schema):
    sql = "WITH active AS (SELECT id FROM users) SELECT id FROM active"
    _, v = _validate(parser, sql, schema)
    assert v.valid


def test_nested_subquery(parser, schema):
    sql = "SELECT * FROM (SELECT id FROM users) AS sub"
    _, v = _validate(parser, sql, schema)
    assert v.valid


def test_ambiguous_column(parser, schema):
    sql = "SELECT id FROM users u JOIN orders o ON u.id = o.user_id"
    _, v = _validate(parser, sql, schema)
    assert not v.valid


def test_write_valid(parser, schema):
    sql = "UPDATE products SET price = :price WHERE id = :id"
    c, v = _validate(parser, sql, schema)
    assert c.kind == "write" and v.valid


def test_write_missing_column(parser, schema):
    sql = "UPDATE products SET nonexistent = 1 WHERE id = 1"
    _, v = _validate(parser, sql, schema)
    assert not v.valid
