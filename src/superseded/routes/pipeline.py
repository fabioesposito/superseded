from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from superseded.models import HarnessIteration, IssueStatus, Stage, StageResult
from superseded.pipeline.events import PipelineEventManager
from superseded.pipeline.harness import HarnessRunner
from superseded.pipeline.stages import STAGE_DEFINITIONS
from superseded.pipeline.worktree import WorktreeManager
from superseded.routes.deps import Deps, get_deps
from superseded.tickets.reader import list_issues
from superseded.tickets.writer import update_issue_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline")

_cached_runner: HarnessRunner | None = None
_event_manager = PipelineEventManager()


def _get_harness_runner(deps: Deps) -> HarnessRunner:
    global _cached_runner
    if _cached_runner is None:
        from superseded.agents.claude_code import ClaudeCodeAdapter

        agent = ClaudeCodeAdapter(timeout=deps.config.stage_timeout_seconds)
        _cached_runner = HarnessRunner(
            agent=agent,
            repo_path=deps.config.repo_path,
            max_retries=deps.config.max_retries,
            retryable_stages=deps.config.retryable_stages,
            event_manager=_event_manager,
        )
    return _cached_runner


async def _run_stage(deps: Deps, issue_id: str, stage: Stage) -> StageResult:
    issues_dir = str(Path(deps.config.repo_path) / deps.config.issues_dir)
    issues = [i for i in list_issues(issues_dir) if i.id == issue_id]
    if not issues:
        return StageResult(stage=stage, passed=False, error="Issue not found")

    issue = issues[0]
    runner = _get_harness_runner(deps)
    artifacts_path = str(
        Path(deps.config.repo_path) / deps.config.artifacts_dir / issue_id
    )
    Path(artifacts_path).mkdir(parents=True, exist_ok=True)

    worktree_manager = WorktreeManager(deps.config.repo_path)
    needs_worktree = stage in (Stage.BUILD, Stage.VERIFY, Stage.REVIEW)

    stash_ref = None
    worktree_created = False
    if needs_worktree and not worktree_manager.exists(issue_id):
        stash_ref = await worktree_manager.stash_if_dirty()
        await worktree_manager.create(issue_id)
        worktree_created = True

    previous_errors: list[str] = []
    stage_results = await deps.db.get_stage_results(issue_id)
    for sr in stage_results:
        if not sr.get("passed") and sr.get("error"):
            previous_errors.append(sr["error"])

    result = await runner.run_stage_with_retries(
        issue=issue,
        stage=stage,
        artifacts_path=artifacts_path,
        previous_errors=previous_errors if previous_errors else None,
    )

    await deps.db.save_stage_result(issue_id, result)

    existing_iterations = await deps.db.get_harness_iterations(issue_id)
    attempt_num = len([i for i in existing_iterations if i.get("stage") == stage.value])

    iteration = HarnessIteration(
        attempt=attempt_num,
        stage=stage,
        previous_errors=previous_errors,
    )
    await deps.db.save_harness_iteration(
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
                await worktree_manager.cleanup(issue_id)
        await deps.db.update_issue_status(issue_id, IssueStatus.IN_PROGRESS, stage)
        update_issue_status(issue.filepath, IssueStatus.IN_PROGRESS, stage)
    else:
        await deps.db.update_issue_status(issue_id, IssueStatus.PAUSED, stage)
        update_issue_status(issue.filepath, IssueStatus.PAUSED, stage)
        if stash_ref:
            await worktree_manager.pop_stash(stash_ref)

    return result


@router.post("/issues/{issue_id}/advance")
async def advance_issue(
    request: Request, issue_id: str, deps: Deps = Depends(get_deps)
):
    issues_dir = str(Path(deps.config.repo_path) / deps.config.issues_dir)
    issues = [i for i in list_issues(issues_dir) if i.id == issue_id]
    if not issues:
        return RedirectResponse(url="/", status_code=303)

    issue = issues[0]
    result = await _run_stage(deps, issue_id, issue.stage)

    if result.passed:
        next_stage = issue.next_stage()
        if next_stage is None:
            await deps.db.update_issue_status(issue_id, IssueStatus.DONE, Stage.SHIP)
            update_issue_status(issue.filepath, IssueStatus.DONE, Stage.SHIP)
        else:
            await deps.db.update_issue_status(
                issue_id, IssueStatus.IN_PROGRESS, next_stage
            )
            update_issue_status(issue.filepath, IssueStatus.IN_PROGRESS, next_stage)

    return RedirectResponse(url=f"/issues/{issue_id}", status_code=303)


@router.post("/issues/{issue_id}/retry")
async def retry_issue(request: Request, issue_id: str, deps: Deps = Depends(get_deps)):
    issues_dir = str(Path(deps.config.repo_path) / deps.config.issues_dir)
    issues = [i for i in list_issues(issues_dir) if i.id == issue_id]
    if not issues:
        return RedirectResponse(url="/", status_code=303)

    issue = issues[0]
    result = await _run_stage(deps, issue_id, issue.stage)

    if result.passed:
        next_stage = issue.next_stage()
        if next_stage is None:
            await deps.db.update_issue_status(issue_id, IssueStatus.DONE, Stage.SHIP)
            update_issue_status(issue.filepath, IssueStatus.DONE, Stage.SHIP)
        else:
            await deps.db.update_issue_status(
                issue_id, IssueStatus.IN_PROGRESS, next_stage
            )
            update_issue_status(issue.filepath, IssueStatus.IN_PROGRESS, next_stage)
    else:
        await deps.db.update_issue_status(issue_id, IssueStatus.PAUSED, issue.stage)

    return RedirectResponse(url=f"/issues/{issue_id}", status_code=303)


@router.get("/events")
async def pipeline_events(request: Request, deps: Deps = Depends(get_deps)):
    from sse_starlette.sse import EventSourceResponse

    async def event_generator():
        last_hash = None
        while True:
            if await request.is_disconnected():
                break
            issues = await deps.db.list_issues()
            data = json.dumps(issues)
            current_hash = str(hash(data))
            if current_hash != last_hash:
                last_hash = current_hash
                yield {"event": "update", "data": data}
            await asyncio.sleep(2)

    return EventSourceResponse(event_generator())


@router.get("/issues/{issue_id}/events")
async def get_historical_events(
    request: Request, issue_id: str, deps: Deps = Depends(get_deps)
):
    events = await deps.db.get_agent_events(issue_id)
    return events


@router.get("/issues/{issue_id}/events/stream")
async def stream_events(
    request: Request, issue_id: str, deps: Deps = Depends(get_deps)
):
    from sse_starlette.sse import EventSourceResponse

    async def event_generator():
        async for event in _event_manager.subscribe(issue_id):
            yield {
                "event": event.event_type,
                "data": json.dumps(
                    {"content": event.content, "metadata": event.metadata}
                ),
            }
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(event_generator())
