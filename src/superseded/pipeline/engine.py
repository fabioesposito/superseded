from __future__ import annotations

import datetime
from pathlib import Path

from superseded.agents.base import AgentAdapter
from superseded.models import AgentContext, AgentResult, Issue, Stage, StageResult


class PipelineEngine:
    def __init__(self, agent: AgentAdapter, repo_path: str, timeout: int = 600) -> None:
        self.agent = agent
        self.repo_path = repo_path
        self.timeout = timeout

    async def run_stage(self, issue: Issue, stage: Stage) -> StageResult:
        from superseded.pipeline.prompts import get_prompt_for_stage

        prompt = get_prompt_for_stage(stage)
        artifacts_path = Path(self.repo_path) / ".superseded" / "artifacts" / issue.id
        artifacts_path.mkdir(parents=True, exist_ok=True)

        context = AgentContext(
            repo_path=self.repo_path,
            issue=issue,
            skill_prompt=prompt,
            artifacts_path=str(artifacts_path),
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
