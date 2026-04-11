from __future__ import annotations

import datetime
from pathlib import Path

from superseded.agents.base import AgentAdapter
from superseded.models import (
    AgentContext,
    AgentResult,
    HarnessIteration,
    Issue,
    Stage,
    StageResult,
)
from superseded.pipeline.context import ContextAssembler


class HarnessRunner:
    def __init__(
        self,
        agent: AgentAdapter,
        repo_path: str,
        max_retries: int = 3,
        retryable_stages: list[str] | None = None,
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
