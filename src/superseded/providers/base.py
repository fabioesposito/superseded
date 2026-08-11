from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

EFFORT_MAP: dict[str, dict[str, str]] = {
    "deepseek": {"low": "low", "medium": "high", "high": "high", "max": "max"},
    "openai": {"low": "low", "medium": "medium", "high": "high", "max": "max"},
    "anthropic": {"low": "low", "medium": "medium", "high": "high", "max": "xhigh"},
}


@dataclass(frozen=True)
class ProviderResponse:
    """A provider's completion result plus the metadata the engine wants."""

    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    raw: object | None = None


class ProviderConfigError(RuntimeError):
    """A provider is misconfigured (e.g. missing API key)."""


class Provider(Protocol):
    """A direct-API model provider. Replaces the subprocess-shaped `Agent`."""

    @property
    def name(self) -> str: ...

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        timeout: float = 600.0,
        temperature: float = 0.0,
        reasoning_effort: Literal["low", "medium", "high", "max"] | None = None,
    ) -> ProviderResponse: ...
