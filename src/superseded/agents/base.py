from __future__ import annotations

from typing import Protocol, runtime_checkable

from superseded.models import AgentContext, AgentResult


@runtime_checkable
class AgentAdapter(Protocol):
    timeout: int

    async def run(self, prompt: str, context: AgentContext) -> AgentResult: ...
