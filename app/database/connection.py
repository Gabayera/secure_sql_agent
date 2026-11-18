from __future__ import annotations

from sqlalchemy import Engine, create_engine


def create_readonly_engine(url: str) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_recycle=1800,
        connect_args={"options": "-c default_transaction_read_only=on"},
    )


def create_writer_engine(url: str) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=2,
        pool_recycle=1800,
    )
