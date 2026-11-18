from __future__ import annotations

import json

import httpx

from app.llm.base import LLMClient, LLMError
from app.models import GeneratedSQL

SYSTEM_PROMPT = """You are a secure SQL generation assistant.

Rules:
1. Generate exactly ONE SQL statement.
2. Use only tables and columns present in the provided schema.
3. Use PostgreSQL dialect.
4. Prefer explicit column names over SELECT *.
5. Always use bind parameters for user-provided values (e.g. :price).
6. Never invent table or column names.
7. Never generate multiple statements.
8. Never attempt to bypass permissions.
9. Never generate DDL unless explicitly enabled.
10. Never execute SQL; only generate it.

Respond with ONLY a JSON object with three keys:
- "query": the SQL statement, using :name placeholders for parameters
- "parameters": object mapping placeholder names to values
- "explanation": short human-readable description
"""


class OllamaLLMClient(LLMClient):
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5-coder:7b",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    def generate_sql(
        self,
        request: str,
        schema: dict,
        error_context: str | None = None,
    ) -> GeneratedSQL:
        user_payload = {
            "request": request,
            "schema": schema,
            "previous_error": error_context,
        }
        try:
            with httpx.Client(timeout=180.0) as client:
                resp = client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {
                                "role": "user",
                                "content": json.dumps(user_payload, default=str),
                            },
                        ],
                        "format": "json",
                        "stream": False,
                        "options": {"temperature": 0.0},
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc

        content = data.get("message", {}).get("content", "")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Ollama returned invalid JSON: {content!r}") from exc

        try:
            return GeneratedSQL.model_validate(parsed)
        except Exception as exc:
            raise LLMError(f"Ollama returned invalid SQL payload: {exc}") from exc
