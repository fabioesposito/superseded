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
        event_manager: PipelineEventManager | None = None,
    ) -> None:
        self.agent_factory = agent_factory or AgentFactory()
        self.stage_configs = stage_configs or {}
        self.repo_path = repo_path
        self.context_assembler = ContextAssembler(repo_path)
        self.event_manager = event_manager or PipelineEventManager()
        self.worktree_manager = WorktreeManager(repo_path)

    def resolve_agent(self, stage: Stage) -> AgentAdapter:
        config = self.stage_configs.get(stage.value)
        if config:
            return self.agent_factory.create(
                cli=config.cli, model=config.model, sandbox=config.sandbox
            )
        return self.agent_factory.create()

    async def run_stage(
        self,
        issue: Issue,
        stage: Stage,
        artifacts_path: str,
        previous_errors: list[str] | None = None,
        repo: str | None = None,
    ) -> StageResult:
        """Run a stage once."""
        worktree_path = ""
        if repo and self.worktree_manager.exists(issue.id, repo=repo):
            worktree_path = str(self.worktree_manager.get_path(issue.id, repo=repo))

        prompt = self.context_assembler.build(
            stage=stage,
            issue=issue,
            artifacts_path=artifacts_path,
            previous_errors=previous_errors,
            iteration=0,
            target_repo=repo,
        )

        context = AgentContext(
            repo_path=self.repo_path,
            issue=issue,
            skill_prompt=prompt,
            artifacts_path=artifacts_path,
            worktree_path=worktree_path,
            iteration=0,
            previous_errors=previous_errors or [],
        )

        started = datetime.datetime.now(datetime.UTC)
        agent_result: AgentResult = await self.resolve_agent(stage).run(prompt, context)
        finished = datetime.datetime.now(datetime.UTC)

        passed = agent_result.exit_code == 0

        if passed:
            if stage in (Stage.SPEC, Stage.PLAN):
                artifact_file = Path(artifacts_path) / f"{stage.value}.md"
                artifact_file.parent.mkdir(parents=True, exist_ok=True)
                artifact_file.write_text(agent_result.stdout, encoding="utf-8")

            questions_file = Path(artifacts_path) / "questions.md"
            approval_file = Path(artifacts_path) / "approval.md"
            if questions_file.exists():
                return StageResult(
                    stage=stage,
                    passed=False,
                    output=agent_result.stdout,
                    error="awaiting-input",
                    artifacts=[],
                    started_at=started,
                    finished_at=finished,
                )
            if approval_file.exists():
                return StageResult(
                    stage=stage,
                    passed=False,
                    output=agent_result.stdout,
                    error="approval-required",
                    artifacts=[],
                    started_at=started,
                    finished_at=finished,
                )

            return StageResult(
                stage=stage,
                passed=True,
                output=agent_result.stdout,
                error="",
                artifacts=agent_result.files_changed,
                started_at=started,
                finished_at=finished,
            )

        error_msg = (
            agent_result.stderr
            if agent_result.stderr
            else f"Agent exited with code {agent_result.exit_code}"
        )
        return StageResult(
            stage=stage,
            passed=False,
            output=agent_result.stdout,
            error=error_msg,
            artifacts=[],
            started_at=started,
            finished_at=finished,
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
        """Run a stage once with streaming, saving all events to DB."""
        em = event_manager or self.event_manager
        worktree_path = ""
        if repo and self.worktree_manager.exists(issue.id, repo=repo):
            worktree_path = str(self.worktree_manager.get_path(issue.id, repo=repo))

        prompt = self.context_assembler.build(
            stage=stage,
            issue=issue,
            artifacts_path=artifacts_path,
            previous_errors=previous_errors,
            iteration=0,
            target_repo=repo,
        )

        context = AgentContext(
            repo_path=self.repo_path,
            issue=issue,
            skill_prompt=prompt,
            artifacts_path=artifacts_path,
            worktree_path=worktree_path,
            iteration=0,
            previous_errors=previous_errors or [],
        )

        await db.save_session_turn(
            issue.id,
            SessionTurn(
                role="user",
                content=prompt,
                stage=stage,
                attempt=0,
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
                attempt=0,
                metadata={
                    "exit_code": exit_code,
                    "duration_ms": duration_ms,
                },
            ),
        )

        passed = exit_code == 0

        if passed:
            if stage in (Stage.SPEC, Stage.PLAN):
                artifact_file = Path(artifacts_path) / f"{stage.value}.md"
                artifact_file.parent.mkdir(parents=True, exist_ok=True)
                artifact_file.write_text(stdout, encoding="utf-8")

            questions_file = Path(artifacts_path) / "questions.md"
            approval_file = Path(artifacts_path) / "approval.md"
            if questions_file.exists():
                return StageResult(
                    stage=stage,
                    passed=False,
                    output=stdout,
                    error="awaiting-input",
                    artifacts=[],
                    started_at=datetime.datetime.now(datetime.UTC),
                    finished_at=datetime.datetime.now(datetime.UTC),
                )
            if approval_file.exists():
                return StageResult(
                    stage=stage,
                    passed=False,
                    output=stdout,
                    error="approval-required",
                    artifacts=[],
                    started_at=datetime.datetime.now(datetime.UTC),
                    finished_at=datetime.datetime.now(datetime.UTC),
                )

            # Minimum output check — reject trivially empty runs
            min_output_chars = 50
            if len(stdout.strip()) < min_output_chars:
                return StageResult(
                    stage=stage,
                    passed=False,
                    output=stdout,
                    error=(
                        f"Agent produced only {len(stdout.strip())} chars of output "
                        f"(minimum: {min_output_chars}). The agent may not have "
                        f"actually performed the stage work."
                    ),
                    artifacts=[],
                    started_at=datetime.datetime.now(datetime.UTC),
                    finished_at=datetime.datetime.now(datetime.UTC),
                )

            return StageResult(
                stage=stage,
                passed=True,
                output=stdout,
                error="",
                artifacts=[],
                started_at=datetime.datetime.now(datetime.UTC),
                finished_at=datetime.datetime.now(datetime.UTC),
            )

        error_msg = stdout if stdout else f"Agent exited with code {exit_code}"
        return StageResult(
            stage=stage,
            passed=False,
            output=stdout,
            error=error_msg,
            artifacts=[],
            started_at=datetime.datetime.now(datetime.UTC),
            finished_at=datetime.datetime.now(datetime.UTC),
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
