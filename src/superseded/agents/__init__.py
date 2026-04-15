from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from superseded.agents.base import SubprocessAgentAdapter

_registry: dict[str, type[SubprocessAgentAdapter]] = {}


def register_agent(name: str):
    def decorator(cls):
        _registry[name] = cls
        return cls

    return decorator


def get_registry() -> dict[str, type[SubprocessAgentAdapter]]:
    return _registry


from superseded.agents import claude_code, codex, opencode  # noqa: F401,E402
