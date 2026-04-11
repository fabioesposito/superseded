from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from superseded.config import SupersededConfig
from superseded.db import Database
from superseded.models import HarnessIteration, IssueStatus, Stage, StageResult
from superseded.pipeline.harness import HarnessRunner
from superseded.pipeline.stages import STAGE_DEFINITIONS
from superseded.pipeline.worktree import WorktreeManager
from superseded.tickets.reader import list_issues
from superseded.tickets.writer import update_issue_status

router = APIRouter(prefix="/pipeline")

_config: SupersededConfig | None = None
_db: Database | None = None


def set_deps(config: SupersededConfig, db: Database) -> None:
    global _config, _db
    _config = config
    _db = db


def _get_harness_runner() -> HarnessRunner:
    assert _config
    from superseded.agents.claude_code import ClaudeCodeAdapter

    agent = ClaudeCodeAdapter(timeout=_config.stage_timeout_seconds)
    return HarnessRunner(
        agent=agent,
        repo_path=_config.repo_path,
        max_retries=_config.max_retries,
        retryable_stages=_config.retryable_stages,
    )


async def _run_stage(issue_id: str, stage: Stage) -> StageResult:
    assert _config and _db
    issues_dir = str(Path(_config.repo_path) / _config.issues_dir)
    issues = [i for i in list_issues(issues_dir) if i.id == issue_id]
    if not issues:
        return StageResult(stage=stage, passed=False, error="Issue not found")

    issue = issues[0]
    runner = _get_harness_runner()
    artifacts_path = str(Path(_config.repo_path) / _config.artifacts_dir / issue_id)
    Path(artifacts_path).mkdir(parents=True, exist_ok=True)

    worktree_manager = WorktreeManager(_config.repo_path)
    needs_worktree = stage in (Stage.BUILD, Stage.VERIFY, Stage.REVIEW)

    stash_ref = None
    worktree_created = False
    if needs_worktree and not worktree_manager.exists(issue_id):
        stash_ref = worktree_manager.stash_if_dirty()
        worktree_manager.create(issue_id)
        worktree_created = True

    previous_errors: list[str] = []
    stage_results = await _db.get_stage_results(issue_id)
    for sr in stage_results:
        if not sr.get("passed") and sr.get("error"):
            previous_errors.append(sr["error"])

    result = await runner.run_stage_with_retries(
        issue=issue,
        stage=stage,
        artifacts_path=artifacts_path,
        previous_errors=previous_errors if previous_errors else None,
    )

    await _db.save_stage_result(issue_id, result)

    iteration = HarnessIteration(
        attempt=0,
        stage=stage,
        previous_errors=previous_errors,
    )
    await _db.save_harness_iteration(
        issue_id,
        iteration,
        exit_code=0 if result.passed else 1,
        output=result.output,
        error=result.error,
    )

    if result.passed:
        next_stage = issue.next_stage()
        if next_stage is None or stage == Stage.SHIP:
            if worktree_created:
                worktree_manager.cleanup(issue_id)
        update_issue_status(issue.filepath, IssueStatus.IN_PROGRESS, stage)
    else:
        update_issue_status(issue.filepath, IssueStatus.PAUSED, stage)
        await _db.update_issue_status(issue_id, IssueStatus.PAUSED, stage)

    return result


@router.post("/issues/{issue_id}/advance")
async def advance_issue(request: Request, issue_id: str):
    assert _config and _db
    issues_dir = str(Path(_config.repo_path) / _config.issues_dir)
    issues = [i for i in list_issues(issues_dir) if i.id == issue_id]
    if not issues:
        return RedirectResponse(url="/", status_code=303)

    issue = issues[0]
    result = await _run_stage(issue_id, issue.stage)

    if result.passed:
        next_stage = issue.next_stage()
        if next_stage is None:
            await _db.update_issue_status(issue_id, IssueStatus.DONE, Stage.SHIP)
            update_issue_status(issue.filepath, IssueStatus.DONE, Stage.SHIP)
        else:
            await _db.update_issue_status(issue_id, IssueStatus.IN_PROGRESS, next_stage)
            update_issue_status(issue.filepath, IssueStatus.IN_PROGRESS, next_stage)

    return RedirectResponse(url=f"/issues/{issue_id}", status_code=303)


@router.post("/issues/{issue_id}/retry")
async def retry_issue(request: Request, issue_id: str):
    assert _config and _db
    issues_dir = str(Path(_config.repo_path) / _config.issues_dir)
    issues = [i for i in list_issues(issues_dir) if i.id == issue_id]
    if not issues:
        return RedirectResponse(url="/", status_code=303)

    issue = issues[0]
    result = await _run_stage(issue_id, issue.stage)

    if result.passed:
        next_stage = issue.next_stage()
        if next_stage is None:
            await _db.update_issue_status(issue_id, IssueStatus.DONE, Stage.SHIP)
            update_issue_status(issue.filepath, IssueStatus.DONE, Stage.SHIP)
        else:
            await _db.update_issue_status(issue_id, IssueStatus.IN_PROGRESS, next_stage)
            update_issue_status(issue.filepath, IssueStatus.IN_PROGRESS, next_stage)
    else:
        await _db.update_issue_status(issue_id, IssueStatus.PAUSED, issue.stage)

    return RedirectResponse(url=f"/issues/{issue_id}", status_code=303)


@router.get("/events")
async def pipeline_events(request: Request):
    from sse_starlette.sse import EventSourceResponse

    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            assert _db
            issues = await _db.list_issues()
            data = json.dumps(issues)
            yield {"event": "update", "data": data}
            await asyncio.sleep(2)

    return EventSourceResponse(event_generator())
