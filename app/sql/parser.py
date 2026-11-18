from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError


class SQLParseError(ValueError):
    pass


class SQLParser:
    def __init__(self, dialect: str = "postgres") -> None:
        self.dialect = dialect

    def parse(self, sql: str) -> list[exp.Expression]:
        if not sql or not sql.strip():
            raise SQLParseError("Empty SQL statement")
        try:
            statements = sqlglot.parse(sql, read=self.dialect)
        except SqlglotError as exc:
            raise SQLParseError(f"SQL parse error: {exc}") from exc

        statements = [s for s in statements if s is not None]
        if not statements:
            raise SQLParseError("No SQL statement found")
        return statements

    def parse_single(self, sql: str) -> exp.Expression:
        statements = self.parse(sql)
        if len(statements) != 1:
            raise SQLParseError(
                f"Expected exactly one SQL statement, parsed {len(statements)}. "
                "Multi-statement requests are rejected."
            )
        return statements[0]
