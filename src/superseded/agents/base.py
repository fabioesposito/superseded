from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from superseded.models import AgentContext, AgentEvent, AgentResult


@runtime_checkable
class AgentAdapter(Protocol):
    timeout: int

    async def run(self, prompt: str, context: AgentContext) -> AgentResult: ...

    async def run_streaming(
        self, prompt: str, context: AgentContext
    ) -> AsyncIterator[AgentEvent]: ...


class SubprocessAgentAdapter(AgentAdapter, ABC):
    def __init__(self, timeout: int = 600) -> None:
        self.timeout = timeout

    @abstractmethod
    def _build_command(self, prompt: str) -> list[str]: ...

    def _get_stdin_data(self, prompt: str) -> bytes | None:
        """Override to pass prompt via stdin instead of CLI args."""
        return None

    def _get_cwd(self, context: AgentContext) -> str:
        return context.worktree_path or context.repo_path

    async def run(self, prompt: str, context: AgentContext) -> AgentResult:
        cmd = self._build_command(prompt)
        cwd = self._get_cwd(context)
        stdin_data = self._get_stdin_data(prompt)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdin=asyncio.subprocess.PIPE if stdin_data else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin_data), timeout=self.timeout
            )
            return AgentResult(
                exit_code=proc.returncode or 0,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
            )
        except TimeoutError:
            proc.kill()
            return AgentResult(
                exit_code=-1, stdout="", stderr=f"Agent timed out after {self.timeout}s"
            )

    async def run_streaming(self, prompt: str, context: AgentContext) -> AsyncIterator[AgentEvent]:
        cmd = self._build_command(prompt)
        cwd = self._get_cwd(context)
        stdin_data = self._get_stdin_data(prompt)
        start = time.monotonic()

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdin=asyncio.subprocess.PIPE if stdin_data else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            if stdin_data:
                proc.stdin.write(stdin_data)
                proc.stdin.write_eof()
                await proc.stdin.drain()
        except Exception as exc:
            yield AgentEvent(
                event_type="error",
                content=str(exc),
                stage=context.issue.stage,
            )
            yield AgentEvent(
                event_type="status",
                stage=context.issue.stage,
                metadata={"exit_code": -1, "duration_ms": 0},
            )
            return

        async def _read_lines(stream: asyncio.StreamReader, etype: str):
            while True:
                line = await stream.readline()
                if not line:
                    return
                yield AgentEvent(
                    event_type=etype,
                    content=line.decode("utf-8", errors="replace").rstrip("\n"),
                    stage=context.issue.stage,
                )

        stdout_gen = _read_lines(proc.stdout, "stdout")
        stderr_gen = _read_lines(proc.stderr, "stderr")

        tasks: dict[asyncio.Task, str] = {}

        async def _advance(gen, label):
            try:
                return await gen.__anext__(), label
            except StopAsyncIteration:
                return None, label

        tasks[asyncio.create_task(_advance(stdout_gen, "stdout"))] = "stdout"
        tasks[asyncio.create_task(_advance(stderr_gen, "stderr"))] = "stderr"

        timed_out = False
        while tasks:
            remaining = self.timeout - (time.monotonic() - start)
            if remaining <= 0:
                timed_out = True
                proc.kill()
                yield AgentEvent(
                    event_type="error",
                    content=f"Agent timed out after {self.timeout}s",
                    stage=context.issue.stage,
                )
                for t in tasks:
                    t.cancel()
                break

            done, _pending = await asyncio.wait(
                tasks.keys(),
                return_when=asyncio.FIRST_COMPLETED,
                timeout=remaining,
            )

            if not done and not timed_out:
                proc.kill()
                timed_out = True
                yield AgentEvent(
                    event_type="error",
                    content=f"Agent timed out after {self.timeout}s",
                    stage=context.issue.stage,
                )
                for t in tasks:
                    t.cancel()
                break

            for task in done:
                result, label = task.result()
                del tasks[task]
                if result is not None:
                    yield result
                    new_task = asyncio.create_task(
                        _advance(
                            stdout_gen if label == "stdout" else stderr_gen,
                            label,
                        )
                    )
                    tasks[new_task] = label

        elapsed_ms = int((time.monotonic() - start) * 1000)
        exit_code = -1 if timed_out else (proc.returncode or 0)

        if not timed_out:
            try:
                await asyncio.wait_for(proc.wait(), timeout=1)
            except TimeoutError:
                proc.kill()
                exit_code = -1

        yield AgentEvent(
            event_type="status",
            stage=context.issue.stage,
            metadata={"exit_code": exit_code, "duration_ms": elapsed_ms},
        )
