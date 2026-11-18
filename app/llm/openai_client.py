from __future__ import annotations

import json

from openai import OpenAI

from app.llm.base import LLMClient, LLMError
from app.models import GeneratedSQL

SYSTEM_PROMPT = """You are a secure SQL generation assistant.

Rules:
1. Generate exactly ONE SQL statement.
2. Use only tables and columns present in the provided schema.
3. Use the configured SQL dialect.
4. Prefer explicit column names over SELECT *.
5. Always use bind parameters for user-provided values.
6. Never invent table or column names.
7. Never generate multiple statements.
8. Never attempt to bypass permissions.
9. Never generate DDL unless explicitly enabled.
10. Never execute SQL; only generate it.

Return a GeneratedSQL object with query, parameters, and explanation.
"""


class OpenAILLMClient(LLMClient):
    def __init__(self, api_key: str, model: str) -> None:
        self.client = OpenAI(api_key=api_key)
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
            completion = self.client.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(user_payload, default=str)},
                ],
                response_format=GeneratedSQL,
            )
        except Exception as exc:
            raise LLMError(f"OpenAI request failed: {exc}") from exc

        message = completion.choices[0].message
        if message.parsed is not None:
            return message.parsed
        raise LLMError(message.refusal or "The model did not return SQL")
