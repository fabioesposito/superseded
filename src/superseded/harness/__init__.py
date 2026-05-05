from __future__ import annotations

import asyncio
import datetime
import logging
import os
from pathlib import Path

from superseded.agents.base import AgentAdapter
from superseded.agents.factory import AgentFactory
from superseded.config import RepoEntry, StageAgentConfig, SupersededConfig
from superseded.db import Database
from superseded.harness.checkpoint import Checkpoint, CheckpointManager
from superseded.harness.context import ContextAssembler
from superseded.harness.crg import CRGClient
from superseded.harness.lifecycle import LifecycleManager
from superseded.harness.verification import VerificationEngine
from superseded.models import (
    AgentContext,
    Issue,
    IssueStatus,
    SessionTurn,
    Stage,
    StageResult,
)
from superseded.notifications import NotificationService
from superseded.pipeline.events import PipelineEventManager
from superseded.pipeline.worktree import WorktreeManager
from superseded.state_writer import IssueStateWriter

logger = logging.getLogger(__name__)

MAX_SESSION_TURN_CONTENT_LENGTH = 2000
MIN_OUTPUT_CHARS = 50


class Harness:
    def __init__(
        self,
        repo_path: str,
        agent_factory: AgentFactory | None = None,
        stage_configs: dict[str, StageAgentConfig] | None = None,
        event_manager: PipelineEventManager | None = None,
        db: Database | None = None,
        notification_service: NotificationService | None = None,
    ) -> None:
        self.repo_path = repo_path
        self.agent_factory = agent_factory or AgentFactory()
        self.stage_configs = stage_configs or {}
        self.event_manager = event_manager or PipelineEventManager()
        self.db = db
        self.notification_service = notification_service

        self.context_assembler = ContextAssembler(repo_path)
        self.verification_engine = VerificationEngine()
        self.checkpoint_manager = CheckpointManager(repo_path)
        self.lifecycle_manager = LifecycleManager()
        self.worktree_manager = WorktreeManager(repo_path)
        self.crg_client = CRGClient(repo_path)

    async def _ensure_crg_built(self) -> None:
        if not self.crg_client.available:
            return
        if not self.crg_client.is_built():
            logger.info("CRG graph not found, building %s", self.repo_path)
            await self.crg_client.build()
        elif self.crg_client.is_stale():
            logger.info("CRG graph stale, updating %s", self.repo_path)
            await self.crg_client.update()

    def resolve_agent(self, stage: Stage) -> AgentAdapter:
        config = self.stage_configs.get(stage.value)
        if config:
            return self.agent_factory.create(
                cli=config.cli, model=config.model, sandbox=config.sandbox, rtk=config.rtk
            )
        return self.agent_factory.create()

    def configure_repos(self, repos: dict[str, RepoEntry]) -> None:
        for name, entry in repos.items():
            self.worktree_manager.register_repo(name, entry.path)
            self.context_assembler.register_repo(name, entry.path)

    async def run_stage(
        self,
        issue: Issue,
        stage: Stage,
        config: SupersededConfig,
    ) -> StageResult:
        artifacts_path = str(Path(config.repo_path) / config.artifacts_dir / issue.id)
        Path(artifacts_path).mkdir(parents=True, exist_ok=True)

        needs_worktree = stage in (Stage.BUILD, Stage.VERIFY, Stage.REVIEW)
        if stage == Stage.PLAN and issue.repos:
            needs_worktree = True
        target_repos = issue.repos if issue.repos else [None]

        started_at = datetime.datetime.now(datetime.UTC)
        all_passed = True
        combined_output: list[str] = []

        for repo_name in target_repos:
            result = await self._run_single_repo(
                issue, stage, artifacts_path, repo_name, needs_worktree
            )
            combined_output.append(f"[{repo_name or 'primary'}] {result.output or result.error}")
            if not result.passed:
                all_passed = False

        aggregate = StageResult(
            stage=stage,
            passed=all_passed,
            output="\n".join(combined_output),
            error="" if all_passed else "One or more repos failed",
            started_at=started_at,
            finished_at=datetime.datetime.now(datetime.UTC),
        )

        await self._send_notifications(issue, stage, aggregate, config)

        if all_passed:
            writer = IssueStateWriter(self.db, self.repo_path)
            await writer.write_status(issue.id, issue.filepath, IssueStatus.IN_PROGRESS, stage)
        else:
            writer = IssueStateWriter(self.db, self.repo_path)
            await writer.write_status(issue.id, issue.filepath, IssueStatus.PAUSED, stage)

        return aggregate

    async def _run_single_repo(
        self,
        issue: Issue,
        stage: Stage,
        artifacts_path: str,
        repo_name: str | None,
        needs_worktree: bool,
    ) -> StageResult:
        effective_repo = repo_name or "primary"
        repo_artifacts = str(Path(artifacts_path) / effective_repo)
        Path(repo_artifacts).mkdir(parents=True, exist_ok=True)

        stage_config = self.stage_configs.get(stage.value)
        if stage_config and stage_config.require_approval:
            approval_file = Path(repo_artifacts) / "approval.md"
            if not approval_file.exists():
                approval_file.write_text(
                    f"Stage {stage.value} requires manual approval to proceed.\n\nPlease review the current state and approve to continue.",
                    encoding="utf-8",
                )
                await self.db.update_pause_reason(issue.id, "approval-required")
                result = StageResult(
                    stage=stage,
                    passed=False,
                    output="",
                    error="approval-required",
                    started_at=datetime.datetime.now(datetime.UTC),
                    finished_at=datetime.datetime.now(datetime.UTC),
                )
                await self.db.save_stage_result(issue.id, result, repo=effective_repo)
                return result

        stash_ref = None
        worktree_created = False

        if stage == Stage.SHIP:
            ok, msg = await self._check_gh_auth(self.agent_factory.github_token)
            if not ok:
                result = StageResult(
                    stage=stage,
                    passed=False,
                    output="",
                    error=f"gh auth failed: {msg}",
                )
                await self.db.save_stage_result(issue.id, result, repo=effective_repo)
                return result

        try:
            if needs_worktree and not self.worktree_manager.exists(issue.id, repo=repo_name):
                await self.worktree_manager._ensure_repo_exists(
                    repo_name,
                    github_token=self.agent_factory.github_token,
                )
                await self.worktree_manager.pull(repo=repo_name)
                stash_ref = await self.worktree_manager.stash_if_dirty(repo=repo_name)
                await self.worktree_manager.create(issue.id, repo=repo_name)
                worktree_created = True
        except Exception:
            if stash_ref:
                await self.worktree_manager.pop_stash(stash_ref, repo=repo_name)
            raise

        repo_previous_errors = await self._collect_previous_errors(issue.id, effective_repo)

        result = await self._run_stage_streaming(
            issue=issue,
            stage=stage,
            artifacts_path=repo_artifacts,
            previous_errors=repo_previous_errors if repo_previous_errors else None,
            repo=repo_name,
        )

        if not result.passed:
            questions_file = Path(repo_artifacts) / "questions.md"
            approval_file = Path(repo_artifacts) / "approval.md"
            if questions_file.exists():
                await self.db.update_pause_reason(issue.id, "awaiting-input")
            elif approval_file.exists():
                await self.db.update_pause_reason(issue.id, "approval-required")
            else:
                await self.db.update_pause_reason(issue.id, "failed")
        else:
            approval_file = Path(repo_artifacts) / "approval.md"
            if approval_file.exists():
                result.passed = False
                result.error = "approval-required"
                await self.db.update_pause_reason(issue.id, "approval-required")
            else:
                await self.db.update_pause_reason(issue.id, "")

        await self.db.save_stage_result(issue.id, result, repo=effective_repo)

        if not result.passed and stash_ref:
            await self.worktree_manager.pop_stash(stash_ref, repo=repo_name)

        if result.passed and worktree_created:
            next_stage = issue.next_stage()
            if next_stage is None or stage == Stage.SHIP:
                await self.worktree_manager.cleanup(issue.id, repo=repo_name)

        return result

    async def _run_stage_streaming(
        self,
        issue: Issue,
        stage: Stage,
        artifacts_path: str,
        previous_errors: list[str] | None = None,
        repo: str | None = None,
    ) -> StageResult:
        em = self.event_manager
        worktree_path = ""
        if repo and self.worktree_manager.exists(issue.id, repo=repo):
            worktree_path = str(self.worktree_manager.get_path(issue.id, repo=repo))

        checkpoint = self.checkpoint_manager.load(issue.id, stage.value)
        resume_context = ""
        if checkpoint:
            resume_context = (
                f"\n\n## Resuming from Checkpoint\n"
                f"Completed tasks: {', '.join(checkpoint.completed_tasks) or 'none'}\n"
                f"Current task: {checkpoint.current_task or 'unknown'}\n"
                f"Continue from where you left off.\n"
            )

        crg_enabled = self.crg_client.available
        if crg_enabled:
            await self._ensure_crg_built()

        prompt = self.context_assembler.build(
            stage=stage,
            issue=issue,
            artifacts_path=artifacts_path,
            previous_errors=previous_errors,
            iteration=0,
            target_repo=repo,
            crg_enabled=crg_enabled,
        )
        if resume_context:
            prompt += resume_context

        context = AgentContext(
            repo_path=self.repo_path,
            issue=issue,
            skill_prompt=prompt,
            artifacts_path=artifacts_path,
            worktree_path=worktree_path,
            iteration=0,
            previous_errors=previous_errors or [],
        )

        await self.db.save_session_turn(
            issue.id,
            SessionTurn(role="user", content=prompt, stage=stage, attempt=0),
        )

        em.start(issue.id)
        stdout_parts: list[str] = []
        exit_code = -1
        duration_ms = 0

        try:
            async for event in self.resolve_agent(stage).run_streaming(prompt, context):
                await self.db.save_agent_event(issue.id, event)
                await em.publish(issue.id, event)

                if event.event_type == "stdout":
                    stdout_parts.append(event.content)
                elif event.event_type == "status":
                    exit_code = event.metadata.get("exit_code", -1)
                    duration_ms = event.metadata.get("duration_ms", 0)
        finally:
            em.stop(issue.id)

        stdout = "\n".join(stdout_parts)

        await self.db.save_session_turn(
            issue.id,
            SessionTurn(
                role="assistant",
                content=stdout[:MAX_SESSION_TURN_CONTENT_LENGTH],
                stage=stage,
                attempt=0,
                metadata={"exit_code": exit_code, "duration_ms": duration_ms},
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

            if len(stdout.strip()) < MIN_OUTPUT_CHARS:
                return StageResult(
                    stage=stage,
                    passed=False,
                    output=stdout,
                    error=(
                        f"Agent produced only {len(stdout.strip())} chars of output "
                        f"(minimum: {MIN_OUTPUT_CHARS}). The agent may not have "
                        f"actually performed the stage work."
                    ),
                    artifacts=[],
                    started_at=datetime.datetime.now(datetime.UTC),
                    finished_at=datetime.datetime.now(datetime.UTC),
                )

            stage_config = self.stage_configs.get(stage.value)
            if stage_config:
                verify_config = stage_config.verify
                artifact_contents = {}
                artifact_dir = Path(artifacts_path)
                if artifact_dir.exists():
                    for f in artifact_dir.glob("*.md"):
                        artifact_contents[f.name] = f.read_text(encoding="utf-8")
                verification = self.verification_engine.verify(
                    stage.value, stdout, artifact_contents, verify_config
                )
                if not verification.passed:
                    return StageResult(
                        stage=stage,
                        passed=False,
                        output=stdout,
                        error=self.verification_engine.format_errors_for_retry(verification),
                        artifacts=[],
                        started_at=datetime.datetime.now(datetime.UTC),
                        finished_at=datetime.datetime.now(datetime.UTC),
                    )

            self.checkpoint_manager.clear(issue.id, stage.value)

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

    async def _send_notifications(
        self, issue: Issue, stage: Stage, result: StageResult, config: SupersededConfig
    ) -> None:
        if not (
            self.notification_service
            and self.notification_service.enabled
            and self.notification_service.topic
        ):
            return
        duration = ""
        if result.started_at and result.finished_at:
            dur = (result.finished_at - result.started_at).total_seconds()
            duration = f" ({int(dur // 60)}m {int(dur % 60)}s)" if dur >= 60 else f" ({int(dur)}s)"
        if result.passed:
            await self.notification_service.notify(
                title=f"{issue.id}: {stage.value.upper()} completed",
                message=f"Stage {stage.value} passed{duration}",
                priority="default",
                tags=["white_check_mark"],
                click_url=f"{config.base_url}/issues/{issue.id}",
            )
        else:
            await self.notification_service.notify(
                title=f"{issue.id}: {stage.value.upper()} failed",
                message=f"Stage {stage.value} failed: {result.error[:200]}",
                priority="high",
                tags=["x"],
                click_url=f"{config.base_url}/issues/{issue.id}",
            )

    async def _collect_previous_errors(self, issue_id: str, repo: str) -> list[str]:
        stage_results = await self.db.get_stage_results(issue_id, repo=repo)
        return [sr["error"] for sr in stage_results if not sr.get("passed") and sr.get("error")]

    async def _check_gh_auth(self, github_token: str) -> tuple[bool, str]:
        env = os.environ.copy()
        if github_token:
            env["GITHUB_TOKEN"] = github_token
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh",
                "auth",
                "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            _stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                return True, ""
            return False, stderr.decode("utf-8", errors="replace")
        except FileNotFoundError:
            return False, "gh CLI not installed"

    def save_checkpoint(
        self,
        issue_id: str,
        stage: str,
        completed_tasks: list[str],
        current_task: str,
        files_changed: list[str] | None = None,
    ) -> None:
        checkpoint = Checkpoint(
            issue_id=issue_id,
            stage=stage,
            timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
            completed_tasks=completed_tasks,
            current_task=current_task,
            files_changed=files_changed or [],
        )
        self.checkpoint_manager.save(checkpoint)

    def load_checkpoint(self, issue_id: str, stage: str) -> Checkpoint | None:
        return self.checkpoint_manager.load(issue_id, stage)


__all__ = ["Harness"]
