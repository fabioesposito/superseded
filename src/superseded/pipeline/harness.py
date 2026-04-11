from __future__ import annotations

import datetime
from pathlib import Path

from superseded.agents.base import AgentAdapter
from superseded.db import Database
from superseded.models import (
    AgentContext,
    AgentResult,
    HarnessIteration,
    Issue,
    SessionTurn,
    Stage,
    StageResult,
)
from superseded.pipeline.context import ContextAssembler
from superseded.pipeline.events import PipelineEventManager


class HarnessRunner:
    def __init__(
        self,
        agent: AgentAdapter,
        repo_path: str,
        max_retries: int = 3,
        retryable_stages: list[str] | None = None,
        event_manager: PipelineEventManager | None = None,
    ) -> None:
        self.agent = agent
        self.repo_path = repo_path
        self.max_retries = max_retries
        self.retryable_stages = retryable_stages or [
            "build",
            "verify",
            "review",
        ]
        self.context_assembler = ContextAssembler(repo_path)
        self.event_manager = event_manager or PipelineEventManager()

    async def run_stage_with_retries(
        self,
        issue: Issue,
        stage: Stage,
        artifacts_path: str,
        previous_errors: list[str] | None = None,
    ) -> StageResult:
        errors: list[str] = previous_errors or []
        effective_max = self.max_retries if stage.value in self.retryable_stages else 1

        for attempt in range(effective_max):
            prompt = self.context_assembler.build(
                stage=stage,
                issue=issue,
                artifacts_path=artifacts_path,
                previous_errors=errors if errors else None,
                iteration=attempt,
            )

            context = AgentContext(
                repo_path=self.repo_path,
                issue=issue,
                skill_prompt=prompt,
                artifacts_path=artifacts_path,
                iteration=attempt,
                previous_errors=errors,
            )

            started = datetime.datetime.now()
            agent_result: AgentResult = await self.agent.run(prompt, context)
            finished = datetime.datetime.now()

            passed = agent_result.exit_code == 0

            if passed:
                error = ""
                return StageResult(
                    stage=stage,
                    passed=True,
                    output=agent_result.stdout,
                    error=error,
                    artifacts=agent_result.files_changed,
                    started_at=started,
                    finished_at=finished,
                )

            error_msg = (
                agent_result.stderr
                if agent_result.stderr
                else f"Agent exited with code {agent_result.exit_code}"
            )
            errors.append(error_msg)

        combined_errors = "; ".join(errors)
        return StageResult(
            stage=stage,
            passed=False,
            output="",
            error=combined_errors,
            artifacts=[],
            started_at=datetime.datetime.now(),
            finished_at=datetime.datetime.now(),
        )

    async def run_stage_streaming(
        self,
        issue: Issue,
        stage: Stage,
        artifacts_path: str,
        db: Database,
        event_manager: PipelineEventManager | None = None,
        previous_errors: list[str] | None = None,
    ) -> StageResult:
        errors: list[str] = previous_errors or []
        effective_max = self.max_retries if stage.value in self.retryable_stages else 1
        em = event_manager or self.event_manager

        for attempt in range(effective_max):
            prompt = self.context_assembler.build(
                stage=stage,
                issue=issue,
                artifacts_path=artifacts_path,
                previous_errors=errors if errors else None,
                iteration=attempt,
            )

            context = AgentContext(
                repo_path=self.repo_path,
                issue=issue,
                skill_prompt=prompt,
                artifacts_path=artifacts_path,
                iteration=attempt,
                previous_errors=errors,
            )

            await db.save_session_turn(
                issue.id,
                SessionTurn(
                    role="user",
                    content=prompt,
                    stage=stage,
                    attempt=attempt,
                ),
            )

            em.start(issue.id)
            stdout_parts: list[str] = []
            exit_code = -1
            duration_ms = 0

            try:
                async for event in self.agent.run_streaming(prompt, context):
                    await db.save_agent_event(issue.id, event)
                    await em.publish(issue.id, event)

                    if event.event_type == "stdout":
                        stdout_parts.append(event.content)
                    elif event.event_type == "status":
                        exit_code = event.metadata.get("exit_code", -1)
                        duration_ms = event.metadata.get("duration_ms", 0)
            finally:
                em.stop(issue.id)

            stdout = "\n".join(stdout_parts)

            await db.save_session_turn(
                issue.id,
                SessionTurn(
                    role="assistant",
                    content=stdout[:2000],
                    stage=stage,
                    attempt=attempt,
                    metadata={
                        "exit_code": exit_code,
                        "duration_ms": duration_ms,
                    },
                ),
            )

            passed = exit_code == 0

            if passed:
                return StageResult(
                    stage=stage,
                    passed=True,
                    output=stdout,
                    error="",
                    artifacts=[],
                    started_at=datetime.datetime.now(),
                    finished_at=datetime.datetime.now(),
                )

            error_msg = stdout if stdout else f"Agent exited with code {exit_code}"
            errors.append(error_msg)

        combined_errors = "; ".join(errors)
        return StageResult(
            stage=stage,
            passed=False,
            output="",
            error=combined_errors,
            artifacts=[],
            started_at=datetime.datetime.now(),
            finished_at=datetime.datetime.now(),
        )
