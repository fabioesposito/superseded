from __future__ import annotations

import os
from typing import Literal

from openai import OpenAI

from superseded.providers.base import EFFORT_MAP, ProviderConfigError, ProviderResponse


class OpenAICompatProvider:
    """Base for OpenAI-compatible providers (DeepSeek, OpenAI).

    Subclasses set: ``name``, ``api_key_env``, ``base_url``, ``default_model``,
    ``effort_key`` (one of EFFORT_MAP's keys). ``OpenAIProvider`` additionally
    overrides ``_call`` and ``_parse_response`` to use the Responses API.
    """

    name: str | None = None
    api_key_env: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    effort_key: str | None = None

    def __init__(self, api_key: str | None = None) -> None:
        if not self.api_key_env:
            raise ProviderConfigError(
                f"{type(self).__name__} is abstract: subclass must set "
                "name, api_key_env, base_url, default_model, effort_key"
            )
        resolved = api_key or os.environ.get(self.api_key_env)
        if not resolved:
            raise ProviderConfigError(
                f"No {self.name} API key. Set ${self.api_key_env} or pass api_key=."
            )
        self._client = OpenAI(api_key=resolved, base_url=self.base_url, max_retries=2)
        self._default_model = self.default_model

    def _map_effort(self, effort: str) -> str | None:
        return EFFORT_MAP[self.effort_key].get(effort)

    def _call(self, kwargs: dict):
        return self._client.chat.completions.create(**kwargs)

    def _parse_response(self, resp, resolved: str) -> ProviderResponse:
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
            "temperature": temperature,
            "timeout": timeout,
        }
        if reasoning_effort is not None:
            mapped = self._map_effort(reasoning_effort)
            if mapped is not None:
                kwargs["reasoning_effort"] = mapped
        resp = self._call(kwargs)
        return self._parse_response(resp, resolved)
