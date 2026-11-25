from __future__ import annotations
 
import json
import re
from abc import ABC, abstractmethod
from typing import Any
 
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
 
 
_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")
 
 
def parse_llm_payload(content: str) -> GeneratedSQL:
    """Turn a model's text answer into GeneratedSQL, tolerating the usual
    small-model quirks: code fences, text around the JSON, "sql" instead of
    "query", "params" instead of "parameters", parameters as a list."""
    raw = (content or "").strip()
    if not raw:
        raise LLMError("The model returned an empty response")
    raw = _FENCE.sub("", raw).strip()
    if not raw.startswith("{"):
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            raise LLMError(f"The model did not return JSON: {content[:200]!r}")
        raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError(f"The model returned invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMError("The model's JSON must be an object")
 
    query = data.get("query", data.get("sql", ""))
    if not isinstance(query, str):
        raise LLMError("'query' must be a string")
 
    params: Any = data.get("parameters", data.get("params", {}))
    if params is None:
        params = {}
    if isinstance(params, list):
        converted: dict[str, Any] = {}
        for item in params:
            if not isinstance(item, dict) or "name" not in item:
                raise LLMError("'parameters' list items must be {name, value} objects")
            converted[str(item["name"]).lstrip(":")] = item.get("value")
        params = converted
    if not isinstance(params, dict):
        raise LLMError("'parameters' must be an object")
    params = {str(k).lstrip(":"): v for k, v in params.items()}
 
    explanation = data.get("explanation", "")
    return GeneratedSQL(
        query=query.strip(),
        parameters=params,
        explanation=explanation if isinstance(explanation, str) else str(explanation),
    )
