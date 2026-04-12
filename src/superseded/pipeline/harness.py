from __future__ import annotations

import datetime
from pathlib import Path

from superseded.agents.base import AgentAdapter
from superseded.agents.factory import AgentFactory
from superseded.config import RepoEntry, StageAgentConfig
from superseded.db import Database
from superseded.models import (
    AgentContext,
    AgentResult,
    Issue,
    SessionTurn,
    Stage,
    StageResult,
)
from superseded.pipeline.context import ContextAssembler
from superseded.pipeline.events import PipelineEventManager
from superseded.pipeline.worktree import WorktreeManager


class HarnessRunner:
    def __init__(
        self,
        repo_path: str,
        agent_factory: AgentFactory | None = None,
        stage_configs: dict[str, StageAgentConfig] | None = None,
        agent: AgentAdapter | None = None,
        max_retries: int = 3,
        retryable_stages: list[str] | None = None,
        event_manager: PipelineEventManager | None = None,
    ) -> None:
        if agent_factory is None and agent is not None:
            _fallback = agent
            agent_factory = AgentFactory()
            agent_factory.create = lambda **kwargs: _fallback
        self.agent_factory = agent_factory or AgentFactory()
        self.stage_configs = stage_configs or {}
        self.repo_path = repo_path
        self.max_retries = max_retries
        self.retryable_stages = retryable_stages or [
            "build",
            "verify",
            "review",
        ]
        self.context_assembler = ContextAssembler(repo_path)
        self.event_manager = event_manager or PipelineEventManager()
        self.worktree_manager = WorktreeManager(repo_path)

    def resolve_agent(self, stage: Stage) -> AgentAdapter:
        config = self.stage_configs.get(stage.value)
        if config:
            return self.agent_factory.create(cli=config.cli, model=config.model)
        return self.agent_factory.create()

    async def run_stage_with_retries(
        self,
        issue: Issue,
        stage: Stage,
        artifacts_path: str,
        previous_errors: list[str] | None = None,
        repo: str | None = None,
    ) -> StageResult:
        errors: list[str] = previous_errors or []
        effective_max = self.max_retries if stage.value in self.retryable_stages else 1

        worktree_path = ""
        if repo and self.worktree_manager.exists(issue.id, repo=repo):
            worktree_path = str(self.worktree_manager.get_path(issue.id, repo=repo))

        for attempt in range(effective_max):
            prompt = self.context_assembler.build(
                stage=stage,
                issue=issue,
                artifacts_path=artifacts_path,
                previous_errors=errors if errors else None,
                iteration=attempt,
                target_repo=repo,
            )

            context = AgentContext(
                repo_path=self.repo_path,
                issue=issue,
                skill_prompt=prompt,
                artifacts_path=artifacts_path,
                worktree_path=worktree_path,
                iteration=attempt,
                previous_errors=errors,
            )

            started = datetime.datetime.now()
            agent_result: AgentResult = await self.resolve_agent(stage).run(prompt, context)
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
                async for event in self.resolve_agent(stage).run_streaming(prompt, context):
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

    def configure_repos(self, repos: dict[str, RepoEntry]) -> None:
        """Register named repos with worktree manager and context assembler."""
        for name, entry in repos.items():
            self.worktree_manager.register_repo(name, entry.path)
            self.context_assembler.register_repo(name, entry.path)

    async def run_stage_multi_repo(
        self,
        issue: Issue,
        stage: Stage,
        artifacts_path: str,
        previous_errors: list[str] | None = None,
    ) -> dict[str, StageResult]:
        """Run a stage once per target repo. Returns {repo_name: StageResult}."""
        if not issue.repos:
            result = await self.run_stage_with_retries(
                issue, stage, artifacts_path, previous_errors
            )
            return {"primary": result}

        results: dict[str, StageResult] = {}
        for repo_name in issue.repos:
            repo_artifacts = str(Path(artifacts_path) / repo_name)
            Path(repo_artifacts).mkdir(parents=True, exist_ok=True)

            repo_errors = list(previous_errors) if previous_errors else None

            result = await self.run_stage_with_retries(
                issue, stage, repo_artifacts, repo_errors, repo=repo_name
            )
            results[repo_name] = result

        return results
