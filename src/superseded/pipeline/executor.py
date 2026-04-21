from __future__ import annotations

import asyncio
import datetime
import logging
import os
from pathlib import Path

from superseded.config import SupersededConfig
from superseded.db import Database
from superseded.models import (
    Issue,
    IssueStatus,
    Stage,
    StageResult,
)
from superseded.notifications import NotificationService
from superseded.pipeline.harness import HarnessRunner
from superseded.pipeline.worktree import WorktreeManager
from superseded.state_writer import IssueStateWriter

logger = logging.getLogger(__name__)


class StageExecutor:
    def __init__(
        self,
        runner: HarnessRunner,
        db: Database,
        worktree_manager: WorktreeManager,
        notification_service: NotificationService | None = None,
    ) -> None:
        self.runner = runner
        self.db = db
        self.worktree_manager = worktree_manager
        self.notification_service = notification_service

    async def run_stage(self, issue: Issue, stage: Stage, config: SupersededConfig) -> StageResult:
        artifacts_path = str(Path(config.repo_path) / config.artifacts_dir / issue.id)
        Path(artifacts_path).mkdir(parents=True, exist_ok=True)

        needs_worktree = stage in (Stage.BUILD, Stage.VERIFY, Stage.REVIEW)
        # Also create worktrees for PLAN when targeting external repos so the
        # agent sandbox can access target repo files from within its working dir.
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

        if (
            self.notification_service
            and self.notification_service.enabled
            and self.notification_service.topic
        ):
            duration = ""
            if aggregate.started_at and aggregate.finished_at:
                dur = (aggregate.finished_at - aggregate.started_at).total_seconds()
                if dur >= 60:
                    duration = f" ({int(dur // 60)}m {int(dur % 60)}s)"
                else:
                    duration = f" ({int(dur)}s)"
            if aggregate.passed:
                await self.notification_service.notify(
                    title=f"{issue.id}: {stage.value.upper()} completed",
                    message=f"Stage {stage.value} passed{duration}",
                    priority="default",
                    tags=["white_check_mark"],
                    click_url=f"http://localhost:8000/issues/{issue.id}",
                )
            else:
                await self.notification_service.notify(
                    title=f"{issue.id}: {stage.value.upper()} failed",
                    message=f"Stage {stage.value} failed: {aggregate.error[:200]}",
                    priority="high",
                    tags=["x"],
                    click_url=f"http://localhost:8000/issues/{issue.id}",
                )

        if all_passed:
            writer = IssueStateWriter(self.db)
            writer._write_markdown(issue.filepath, IssueStatus.IN_PROGRESS, stage)
            await self.db.update_issue_status(issue.id, IssueStatus.IN_PROGRESS, stage)
        else:
            writer = IssueStateWriter(self.db)
            writer._write_markdown(issue.filepath, IssueStatus.PAUSED, stage)
            await self.db.update_issue_status(issue.id, IssueStatus.PAUSED, stage)

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

        stage_config = self.runner.stage_configs.get(stage.value)
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
            ok, msg = await self._check_gh_auth(self.runner.agent_factory.github_token)
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
                    github_token=self.runner.agent_factory.github_token,
                )
                stash_ref = await self.worktree_manager.stash_if_dirty(repo=repo_name)
                await self.worktree_manager.create(issue.id, repo=repo_name)
                worktree_created = True
        except Exception:
            if stash_ref:
                await self.worktree_manager.pop_stash(stash_ref, repo=repo_name)
            raise

        repo_previous_errors = await self._collect_previous_errors(issue.id, effective_repo)

        result = await self.runner.run_stage_streaming(
            issue=issue,
            stage=stage,
            artifacts_path=repo_artifacts,
            db=self.db,
            event_manager=self.runner.event_manager,
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
