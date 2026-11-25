from __future__ import annotations
 
from openai import BadRequestError, OpenAI
 
from app.llm.base import LLMClient, LLMError, parse_llm_payload
from app.llm.prompts import PromptOptions, build_system_prompt, build_user_prompt
from app.models import GeneratedSQL
 
 
class OpenAILLMClient(LLMClient):
    """Uses JSON mode instead of ``chat.completions.parse(response_format=GeneratedSQL)``.
 
    The old structured-output call could not work: ``parameters: dict[str, Any]``
    is not allowed by OpenAI's strict JSON schema (additionalProperties must
    be false), so every request failed. JSON mode also works with
    OpenAI-compatible servers (set OPENAI_BASE_URL).
    """
 
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: float = 120.0,
        options: PromptOptions | None = None,
    ) -> None:
        kwargs: dict = {"api_key": api_key, "timeout": timeout}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)
        self.model = model
        self.options = options or PromptOptions()
        self.system_prompt = build_system_prompt(self.options)
        self._send_temperature = True
 
    def generate_sql(
        self,
        request: str,
        schema: dict,
        error_context: str | None = None,
    ) -> GeneratedSQL:
        messages = [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "user",
                "content": build_user_prompt(request, schema, self.options, error_context),
            },
        ]
        try:
            completion = self._create(messages)
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"OpenAI request failed: {exc}") from exc
 
        message = completion.choices[0].message
        if getattr(message, "refusal", None):
            raise LLMError(f"The model refused: {message.refusal}")
        return parse_llm_payload(message.content or "")
 
    def _create(self, messages: list[dict]):
        kwargs = {
            "model": self.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        if self._send_temperature:
            kwargs["temperature"] = 0
        try:
            return self.client.chat.completions.create(**kwargs)
        except BadRequestError as exc:
            # Some reasoning models only accept the default temperature.
            if self._send_temperature and "temperature" in str(exc).lower():
                self._send_temperature = False
                kwargs.pop("temperature", None)
                return self.client.chat.completions.create(**kwargs)
            raise
