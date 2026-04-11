from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from superseded.models import AgentContext, AgentResult


@runtime_checkable
class AgentAdapter(Protocol):
    timeout: int

    async def run(self, prompt: str, context: AgentContext) -> AgentResult: ...


class SubprocessAgentAdapter(AgentAdapter, ABC):
    def __init__(self, timeout: int = 600) -> None:
        self.timeout = timeout

    @abstractmethod
    def _build_command(self, prompt: str) -> list[str]: ...

    def _get_cwd(self, context: AgentContext) -> str:
        return context.worktree_path or context.repo_path

    async def run(self, prompt: str, context: AgentContext) -> AgentResult:
        cmd = self._build_command(prompt)
        cwd = self._get_cwd(context)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
            return AgentResult(
                exit_code=proc.returncode or 0,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
            )
        except asyncio.TimeoutError:
            proc.kill()
            return AgentResult(
                exit_code=-1, stdout="", stderr=f"Agent timed out after {self.timeout}s"
            )
