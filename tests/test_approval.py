from __future__ import annotations

import pytest

from app.models import ApprovalDecision, ApprovalRequest
from app.security.approval import (
    ApprovalError,
    ApprovalManager,
    ApprovalToken,
)


class _YesProvider:
    def request(self, request):
        return ApprovalDecision(approved=True)


class _NoProvider:
    def request(self, request):
        return ApprovalDecision(approved=False)


def _request():
    return ApprovalRequest(
        request_id="req-1",
        operation="UPDATE",
        sql="UPDATE products SET price = :price WHERE id = :id",
        parameters={"price": 250, "id": 15},
        explanation="Update price",
        tables=["products"],
    )


def test_approved_token_created_and_verified():
    manager = ApprovalManager(_YesProvider())
    request = _request()
    token = manager.create_token(request)
    assert isinstance(token, ApprovalToken)
    manager.verify_and_consume(token.token, request.sql, request.parameters)


def test_rejected_write_does_not_get_token():
    manager = ApprovalManager(_NoProvider())
    assert manager.provider.request(_request()).approved is False


def test_token_cannot_be_reused():
    manager = ApprovalManager(_YesProvider())
    request = _request()
    token = manager.create_token(request)
    manager.verify_and_consume(token.token, request.sql, request.parameters)
    with pytest.raises(ApprovalError):
        manager.verify_and_consume(token.token, request.sql, request.parameters)


def test_modified_sql_requires_new_approval():
    manager = ApprovalManager(_YesProvider())
    request = _request()
    token = manager.create_token(request)
    with pytest.raises(ApprovalError):
        manager.verify_and_consume(
            token.token,
            "UPDATE products SET price = 999 WHERE id = 15",
            request.parameters,
        )


def test_modified_parameters_require_new_approval():
    manager = ApprovalManager(_YesProvider())
    request = _request()
    token = manager.create_token(request)
    with pytest.raises(ApprovalError):
        manager.verify_and_consume(token.token, request.sql, {"price": 999, "id": 15})


def test_no_provider_rejects_writes():
    assert ApprovalManager(None).provider is None
