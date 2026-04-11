from __future__ import annotations

import asyncio

from superseded.models import AgentContext, AgentResult


class OpenCodeAdapter:
    def __init__(self, timeout: int = 600) -> None:
        self.timeout = timeout

    def _build_command(self, context: AgentContext) -> list[str]:
        return [
            "opencode",
            "--non-interactive",
            context.skill_prompt,
        ]

    async def run(self, prompt: str, context: AgentContext) -> AgentResult:
        cmd = self._build_command(context)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=context.repo_path,
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
