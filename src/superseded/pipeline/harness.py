from __future__ import annotations

from pathlib import Path

from superseded.agents.base import AgentAdapter
from superseded.agents.factory import AgentFactory
from superseded.config import RepoEntry, StageAgentConfig
from superseded.db import Database
from superseded.harness import Harness
from superseded.harness.lifecycle import ResourceLimits
from superseded.models import Issue, Stage, StageResult
from superseded.pipeline.events import PipelineEventManager

MAX_SESSION_TURN_CONTENT_LENGTH = 2000
MIN_OUTPUT_CHARS = 50


class HarnessRunner:
    """Backward-compatible wrapper around Harness."""

    def __init__(
        self,
        repo_path: str,
        agent_factory: AgentFactory | None = None,
        stage_configs: dict[str, StageAgentConfig] | None = None,
        event_manager: PipelineEventManager | None = None,
        db: Database | None = None,
        auto_retry: bool = False,
        max_auto_retries: int = 1,
    ) -> None:
        self._harness = Harness(
            repo_path=repo_path,
            agent_factory=agent_factory,
            stage_configs=stage_configs,
            event_manager=event_manager,
            db=db,
        )
        self._harness.auto_retry = auto_retry
        self._harness.max_auto_retries = max_auto_retries
        self.agent_factory = self._harness.agent_factory
        self.stage_configs = self._harness.stage_configs
        self.event_manager = self._harness.event_manager
        self.repo_path = repo_path

    @property
    def context_assembler(self):
        return self._harness.context_assembler

    @property
    def worktree_manager(self):
        return self._harness.worktree_manager

    @property
    def verification_engine(self):
        return self._harness.verification_engine

    def resolve_agent(self, stage: Stage) -> AgentAdapter:
        return self._harness.resolve_agent(stage)

    def configure_repos(self, repos: dict[str, RepoEntry]) -> None:
        self._harness.configure_repos(repos)

    async def run_stage(
        self,
        issue: Issue,
        stage: Stage,
        artifacts_path: str,
        previous_errors: list[str] | None = None,
        repo: str | None = None,
        resource_limits: ResourceLimits | None = None,
    ) -> StageResult:
        return await self._harness._run_stage_streaming(
            issue=issue,
            stage=stage,
            artifacts_path=artifacts_path,
            previous_errors=previous_errors,
            repo=repo,
            resource_limits=resource_limits,
        )

    async def run_stage_streaming(
        self,
        issue: Issue,
        stage: Stage,
        artifacts_path: str,
        db: Database,
        event_manager: PipelineEventManager | None = None,
        previous_errors: list[str] | None = None,
        repo: str | None = None,
    ) -> StageResult:
        prev_db = self._harness.db
        self._harness.db = db
        try:
            return await self._harness._run_stage_streaming(
                issue=issue,
                stage=stage,
                artifacts_path=artifacts_path,
                previous_errors=previous_errors,
                repo=repo,
            )
        finally:
            self._harness.db = prev_db

    async def run_stage_multi_repo(
        self,
        issue: Issue,
        stage: Stage,
        artifacts_path: str,
        previous_errors: list[str] | None = None,
    ) -> dict[str, StageResult]:
        if not issue.repos:
            result = await self.run_stage(issue, stage, artifacts_path, previous_errors)
            return {"primary": result}

        results: dict[str, StageResult] = {}
        for repo_name in issue.repos:
            repo_artifacts = str(Path(artifacts_path) / repo_name)
            Path(repo_artifacts).mkdir(parents=True, exist_ok=True)
            repo_errors = list(previous_errors) if previous_errors else None
            result = await self.run_stage(issue, stage, repo_artifacts, repo_errors, repo=repo_name)
            results[repo_name] = result

        return results
