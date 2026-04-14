from __future__ import annotations

import asyncio
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
from superseded.pipeline.harness import HarnessRunner
from superseded.pipeline.worktree import WorktreeManager
from superseded.tickets.writer import update_issue_status

logger = logging.getLogger(__name__)


class StageExecutor:
    def __init__(
        self,
        runner: HarnessRunner,
        db: Database,
        worktree_manager: WorktreeManager,
    ) -> None:
        self.runner = runner
        self.db = db
        self.worktree_manager = worktree_manager

    async def run_stage(self, issue: Issue, stage: Stage, config: SupersededConfig) -> StageResult:
        artifacts_path = str(Path(config.repo_path) / config.artifacts_dir / issue.id)
        Path(artifacts_path).mkdir(parents=True, exist_ok=True)

        needs_worktree = stage in (Stage.BUILD, Stage.VERIFY, Stage.REVIEW)
        # Also create worktrees for PLAN when targeting external repos so the
        # agent sandbox can access target repo files from within its working dir.
        if stage == Stage.PLAN and issue.repos:
            needs_worktree = True
        target_repos = issue.repos if issue.repos else [None]

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
        )

        if all_passed:
            await self.db.update_issue_status(issue.id, IssueStatus.IN_PROGRESS, stage)
            update_issue_status(issue.filepath, IssueStatus.IN_PROGRESS, stage)
        else:
            await self.db.update_issue_status(issue.id, IssueStatus.PAUSED, stage)
            update_issue_status(issue.filepath, IssueStatus.PAUSED, stage)

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

        repo_artifacts = str(Path(artifacts_path) / effective_repo)
        Path(repo_artifacts).mkdir(parents=True, exist_ok=True)

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
            if questions_file.exists():
                await self.db.update_pause_reason(issue.id, "awaiting-input")
            else:
                await self.db.update_pause_reason(issue.id, "failed")
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
