from __future__ import annotations
 
from unittest.mock import MagicMock
 
from app.sql.classifier import SQLClassifier
from app.sql.live_validator import LiveValidator
 
 
class _FakeConnection:
    def __enter__(self):
        return self
 
    def __exit__(self, *args):
        return False
 
    def begin(self):
        return self
 
    def rollback(self):
        pass
 
    def execute(self, *args, **kwargs):
        return MagicMock()
 
 
class _FakeEngine:
    def connect(self):
        return _FakeConnection()
 
 
def test_valid_select(parser):
    tree = parser.parse_single("SELECT id FROM users")
    c = SQLClassifier(dialect="postgres").classify(tree)
    v = LiveValidator(_FakeEngine()).validate(tree, "SELECT id FROM users", {}, c)
    assert v.valid
 
 
def test_write_skipped(parser):
    tree = parser.parse_single("UPDATE users SET name = 'A' WHERE id = 1")
    c = SQLClassifier(dialect="postgres").classify(tree)
    v = LiveValidator(_FakeEngine()).validate(
        tree, "UPDATE users SET name = 'A' WHERE id = 1", {}, c
    )
    assert v.valid
