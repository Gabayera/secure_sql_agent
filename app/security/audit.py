from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.models import AuditRecord

_SENSITIVE_KEY = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|access[_-]?key|credential)",
    re.IGNORECASE,
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("***REDACTED***" if _SENSITIVE_KEY.search(str(k)) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class AuditLogger:
    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger("secure_sql_agent.audit")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        if not any(isinstance(h, logging.FileHandler) for h in self.logger.handlers):
            handler = logging.FileHandler(path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            self.logger.addHandler(handler)

    def log(self, record: AuditRecord) -> None:
        data = record.model_dump(mode="json")
        data["parameters"] = redact(data.get("parameters", {}))
        self.logger.info(json.dumps(data, default=str, ensure_ascii=False))
