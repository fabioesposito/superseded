from __future__ import annotations

import datetime
from pathlib import Path

from superseded.agents.base import AgentAdapter
from superseded.models import AgentContext, AgentResult, Issue, Stage, StageResult
from superseded.pipeline.context import ContextAssembler


class PipelineEngine:
    def __init__(self, agent: AgentAdapter, repo_path: str, timeout: int = 600) -> None:
        self.agent = agent
        self.repo_path = repo_path
        self.timeout = timeout
        self.context_assembler = ContextAssembler(repo_path)

    async def run_stage(
        self,
        issue: Issue,
        stage: Stage,
        artifacts_path: str | None = None,
    ) -> StageResult:
        if artifacts_path is None:
            artifacts_path = str(
                Path(self.repo_path) / ".superseded" / "artifacts" / issue.id
            )
        Path(artifacts_path).mkdir(parents=True, exist_ok=True)

        prompt = self.context_assembler.build(
            stage=stage,
            issue=issue,
            artifacts_path=artifacts_path,
        )

        context = AgentContext(
            repo_path=self.repo_path,
            issue=issue,
            skill_prompt=prompt,
            artifacts_path=artifacts_path,
        )

        started = datetime.datetime.now()
        agent_result: AgentResult = await self.agent.run(prompt, context)
        finished = datetime.datetime.now()

        passed = agent_result.exit_code == 0
        error = ""
        if not passed:
            error = (
                agent_result.stderr
                if agent_result.stderr
                else f"Agent exited with code {agent_result.exit_code}"
            )

        return StageResult(
            stage=stage,
            passed=passed,
            output=agent_result.stdout,
            error=error,
            artifacts=agent_result.files_changed,
            started_at=started,
            finished_at=finished,
        )
