from __future__ import annotations

from abc import ABC, abstractmethod

from app.models import GeneratedSQL


class LLMError(RuntimeError):
    pass


class LLMClient(ABC):
    @abstractmethod
    def generate_sql(
        self,
        request: str,
        schema: dict,
        error_context: str | None = None,
    ) -> GeneratedSQL:
        raise NotImplementedError
