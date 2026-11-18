from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.security.permissions import PermissionChecker, PermissionError_


def _engine_with_user(user: str):
    engine = MagicMock()
    conn = MagicMock()
    conn.execute.return_value.scalar_one.return_value = user
    engine.connect.return_value.__enter__.return_value = conn
    return engine


def test_same_role_rejected():
    ro = _engine_with_user("same_role")
    rw = _engine_with_user("same_role")
    with pytest.raises(PermissionError_):
        PermissionChecker(ro, rw).verify_roles_differ()


def test_different_roles_accepted():
    ro = _engine_with_user("readonly_role")
    rw = _engine_with_user("writer_role")
    PermissionChecker(ro, rw).verify_roles_differ()
