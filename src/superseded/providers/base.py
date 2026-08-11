from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


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
