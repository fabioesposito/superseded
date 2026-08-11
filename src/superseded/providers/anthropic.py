from __future__ import annotations

import os
from typing import Literal

from anthropic import Anthropic

from superseded.providers.base import EFFORT_MAP, ProviderConfigError, ProviderResponse

ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-5"
ANTHROPIC_API_KEY_ENV = "SUPERSEDED_ANTHROPIC_API_KEY"
ANTHROPIC_MAX_TOKENS = 128_000


class AnthropicProvider:
    """Claude via the Anthropic Messages API (adaptive thinking)."""

    name = "anthropic"

    def __init__(self, api_key: str | None = None) -> None:
        resolved = api_key or os.environ.get(ANTHROPIC_API_KEY_ENV)
        if not resolved:
            raise ProviderConfigError(
                f"No anthropic API key. Set ${ANTHROPIC_API_KEY_ENV} or pass api_key=."
            )
        self._client = Anthropic(api_key=resolved, max_retries=2)
        self._default_model = ANTHROPIC_DEFAULT_MODEL

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        timeout: float = 600.0,
        temperature: float = 0.0,
        reasoning_effort: Literal["low", "medium", "high", "max"] | None = None,
    ) -> ProviderResponse:
        resolved = model or self._default_model
        kwargs: dict = {
            "model": resolved,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": ANTHROPIC_MAX_TOKENS,
            "temperature": temperature,
            "timeout": timeout,
        }
        if reasoning_effort is not None:
            mapped = EFFORT_MAP["anthropic"].get(reasoning_effort)
            if mapped is not None:
                # The installed SDK's create() has no `effort` kwarg yet;
                # extra_body is the documented forward-compat escape hatch.
                kwargs["extra_body"] = {"effort": mapped}
        resp = self._client.messages.create(**kwargs)
        # Anthropic returns content as a list of blocks; join text blocks.
        content = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return ProviderResponse(
            content=content,
            prompt_tokens=resp.usage.input_tokens,
            completion_tokens=resp.usage.output_tokens,
            model=resp.model,
            raw=resp,
        )
