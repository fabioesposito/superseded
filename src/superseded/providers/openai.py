from __future__ import annotations

from superseded.providers.base import ProviderResponse
from superseded.providers.openai_compat import OpenAICompatProvider

OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
OPENAI_DEFAULT_MODEL = "gpt-5.6-terra"
OPENAI_API_KEY_ENV = "SUPERSEDED_OPENAI_API_KEY"


class OpenAIProvider(OpenAICompatProvider):
    """GPT-5.6 via OpenAI's Responses API."""

    name = "openai"
    api_key_env = OPENAI_API_KEY_ENV
    base_url = OPENAI_DEFAULT_BASE_URL
    default_model = OPENAI_DEFAULT_MODEL
    effort_key = "openai"

    def _call(self, kwargs: dict):
        # Responses API uses `input` (not Chat Completions' `messages`) and
        # accepts a plain string. We always send exactly one user message.
        input_ = kwargs.pop("messages")[0]["content"]
        return self._client.responses.create(input=input_, **kwargs)

    def _parse_response(self, resp, resolved: str) -> ProviderResponse:
        content = getattr(resp, "output_text", None) or ""
        usage = getattr(resp, "usage", None)
        return ProviderResponse(
            content=content,
            prompt_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
            completion_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
            model=getattr(resp, "model", resolved),
            raw=resp,
        )
