from __future__ import annotations

from sqlalchemy import Engine, text


class PermissionError_(RuntimeError):
    pass


class PermissionChecker:
    def __init__(self, readonly_engine: Engine, writer_engine: Engine) -> None:
        self.readonly_engine = readonly_engine
        self.writer_engine = writer_engine

    def verify_roles_differ(self) -> None:
        readonly_role = self._current_user(self.readonly_engine)
        writer_role = self._current_user(self.writer_engine)
        if readonly_role == writer_role:
            raise PermissionError_(
                "Read-only and writer connections use the same database role. "
                "Refusing to start because permission separation is not enforced."
            )

    @staticmethod
    def _current_user(engine: Engine) -> str:
        with engine.connect() as conn:
            return str(conn.execute(text("SELECT current_user")).scalar_one())
