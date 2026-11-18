from __future__ import annotations

import pytest

from app.sql.parser import SQLParseError


def test_valid_select(parser):
    assert parser.parse_single("SELECT id FROM users") is not None


def test_valid_insert(parser):
    assert parser.parse_single("INSERT INTO users (name) VALUES ('A')") is not None


def test_valid_update(parser):
    assert parser.parse_single("UPDATE users SET name = 'B' WHERE id = 1") is not None


def test_valid_delete(parser):
    assert parser.parse_single("DELETE FROM users WHERE id = 1") is not None


def test_invalid_sql(parser):
    with pytest.raises(SQLParseError):
        parser.parse_single("SELECT FROM WHERE")


def test_empty_sql(parser):
    with pytest.raises(SQLParseError):
        parser.parse_single("   ")


def test_multiple_statements(parser):
    with pytest.raises(SQLParseError):
        parser.parse_single("SELECT 1; DROP TABLE users;")
