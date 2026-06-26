from __future__ import annotations

from dataclasses import dataclass

AGENT_PREFERENCE: tuple[str, ...] = ("claude-code", "opencode", "codex")

DEFAULT_MODELS: dict[str, str] = {
    "claude-code": "claude-sonnet-4-6",
    "codex": "gpt-5.4-mini",
}


@dataclass(frozen=True)
class AgentStatus:
    name: str
    available: bool
    binary: str
