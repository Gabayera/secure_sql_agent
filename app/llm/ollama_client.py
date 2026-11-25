from __future__ import annotations
 
import httpx
 
from app.llm.base import LLMClient, LLMError, parse_llm_payload
from app.llm.prompts import PromptOptions, build_system_prompt, build_user_prompt
from app.models import GeneratedSQL
 
 
class OllamaLLMClient(LLMClient):
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5-coder:7b",
        timeout: float = 120.0,
        num_ctx: int = 8192,
        options: PromptOptions | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        # Ollama's default context (2048-4096 tokens) silently truncates a
        # large schema, which makes the model invent tables.
        self.num_ctx = num_ctx
        self.options = options or PromptOptions()
        self.system_prompt = build_system_prompt(self.options)
 
    def generate_sql(
        self,
        request: str,
        schema: dict,
        error_context: str | None = None,
    ) -> GeneratedSQL:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {
                    "role": "user",
                    "content": build_user_prompt(request, schema, self.options, error_context),
                },
            ],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.0, "num_ctx": self.num_ctx},
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(f"{self.base_url}/api/chat", json=payload)
                if resp.status_code == 404:
                    raise LLMError(
                        f"Ollama model '{self.model}' not found. "
                        f"Run: ollama pull {self.model}"
                    )
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError as exc:
            raise LLMError(
                f"Cannot reach Ollama at {self.base_url}. Is 'ollama serve' running?"
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMError(
                f"Ollama did not answer within {self.timeout:.0f}s "
                "(raise LLM_TIMEOUT_SECONDS for slow machines)"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc
        except ValueError as exc:
            raise LLMError(f"Ollama returned a non-JSON HTTP body: {exc}") from exc
 
        content = (data.get("message") or {}).get("content", "")
        return parse_llm_payload(content)
