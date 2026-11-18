from __future__ import annotations

import pytest

from app.sql.parser import SQLParser

SCHEMA = {
    "public": {
        "users": {
            "id": "INTEGER",
            "name": "VARCHAR",
            "email": "VARCHAR",
            "created_at": "TIMESTAMP",
        },
        "orders": {
            "id": "INTEGER",
            "customer_id": "INTEGER",
            "user_id": "INTEGER",
            "amount": "DECIMAL",
            "created_at": "TIMESTAMP",
        },
        "products": {"id": "INTEGER", "name": "VARCHAR", "price": "DECIMAL"},
        "customers": {
            "id": "INTEGER",
            "name": "VARCHAR",
            "email": "VARCHAR",
            "created_at": "TIMESTAMP",
        },
    }
}


@pytest.fixture()
def schema():
    return SCHEMA


@pytest.fixture()
def parser():
    return SQLParser(dialect="postgres")
