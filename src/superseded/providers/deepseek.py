from __future__ import annotations

import os
from typing import Literal

from openai import OpenAI

from superseded.providers.base import ProviderConfigError, ProviderResponse

DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"
DEEPSEEK_API_KEY_ENV = "SUPERSEDED_DEEPSEEK_API_KEY"


class DeepSeekProvider:
    """Direct DeepSeek Chat Completions client (OpenAI-compatible)."""

    name = "deepseek"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEEPSEEK_DEFAULT_BASE_URL,
        default_model: str = DEEPSEEK_DEFAULT_MODEL,
    ) -> None:
        resolved = api_key or os.environ.get(DEEPSEEK_API_KEY_ENV)
        if not resolved:
            raise ProviderConfigError(
                f"No DeepSeek API key. Set ${DEEPSEEK_API_KEY_ENV} or pass api_key=."
            )
        self._client = OpenAI(api_key=resolved, base_url=base_url, max_retries=2)
        self._default_model = default_model

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        timeout: float = 600.0,
        temperature: float = 0.0,
        reasoning_effort: Literal["low", "high", "max"] | None = None,
    ) -> ProviderResponse:
        resolved = model or self._default_model
        # Thinking mode (enabled by default at effort "high") ignores
        # temperature/top_p silently; reasoning_effort="max" maximizes
        # chain-of-thought quality at the cost of latency/tokens.
        kwargs: dict = {
            "model": resolved,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "timeout": timeout,
        }
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort
        resp = self._client.chat.completions.create(**kwargs)
        # DeepSeek reasoner models populate both .reasoning_content (CoT) and
        # .content. We always read .content — the JSON we want is there
        # regardless of model, so the same code path handles chat and reasoner.
        message = resp.choices[0].message
        content = getattr(message, "content", None) or ""
        usage = getattr(resp, "usage", None)
        return ProviderResponse(
            content=content,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            model=getattr(resp, "model", resolved),
            raw=resp,
        )
