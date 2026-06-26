from __future__ import annotations

import shutil
from dataclasses import dataclass

from superseded.review.engine import AGENT_MAP

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


def detect_agents() -> list[AgentStatus]:
    """Probe all registered agents; return one AgentStatus per agent (any order)."""
    statuses: list[AgentStatus] = []
    for name, cls in AGENT_MAP.items():
        agent = cls(model=None)
        binary = agent.build_command()[0]
        statuses.append(AgentStatus(name=name, available=agent.is_available(), binary=binary))
    return statuses


def detect_gh() -> bool:
    return shutil.which("gh") is not None


def pick_agent(available: list[str]) -> str | None:
    """Return the highest-preference agent name present in `available`, else None."""
    for name in AGENT_PREFERENCE:
        if name in available:
            return name
    return None


def default_model_for(agent: str) -> str | None:
    return DEFAULT_MODELS.get(agent)
