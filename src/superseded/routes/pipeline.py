from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from superseded.models import (
    IssueStatus,
    PipelineMetrics,
    Stage,
)
from superseded.routes import get_templates
from superseded.routes.deps import Deps, get_deps
from superseded.tickets.reader import list_issues
from superseded.tickets.writer import update_issue_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline")


def _get_executor(deps: Deps):
    if deps.pipeline is None:
        raise RuntimeError("Pipeline not initialized")
    return deps.pipeline.executor


def _get_event_manager(deps: Deps):
    if deps.pipeline is None:
        raise RuntimeError("Pipeline not initialized")
    return deps.pipeline.event_manager


def _find_issue(deps: Deps, issue_id: str):
    issues_dir = str(Path(deps.config.repo_path) / deps.config.issues_dir)
    matching = [i for i in list_issues(issues_dir) if i.id == issue_id]
    return matching[0] if matching else None


async def _run_and_advance(deps: Deps, issue_id: str, stage: Stage) -> RedirectResponse:
    issue = _find_issue(deps, issue_id)
    if issue is None:
        return RedirectResponse(url="/", status_code=303)

    executor = _get_executor(deps)
    result = await executor.run_stage(issue, stage, deps.config)

    if result.passed:
        next_stage = issue.next_stage()
        if next_stage is None:
            await deps.db.update_issue_status(issue_id, IssueStatus.DONE, Stage.SHIP)
            update_issue_status(issue.filepath, IssueStatus.DONE, Stage.SHIP)
        else:
            await deps.db.update_issue_status(issue_id, IssueStatus.IN_PROGRESS, next_stage)
            update_issue_status(issue.filepath, IssueStatus.IN_PROGRESS, next_stage)

    return RedirectResponse(url=f"/issues/{issue_id}", status_code=303)


@router.post("/issues/{issue_id}/advance")
async def advance_issue(request: Request, issue_id: str, deps: Deps = Depends(get_deps)):
    issue = _find_issue(deps, issue_id)
    if issue is None:
        return RedirectResponse(url="/", status_code=303)
    return await _run_and_advance(deps, issue_id, issue.stage)


@router.post("/issues/{issue_id}/retry")
async def retry_issue(request: Request, issue_id: str, deps: Deps = Depends(get_deps)):
    issue = _find_issue(deps, issue_id)
    if issue is None:
        return RedirectResponse(url="/", status_code=303)

    executor = _get_executor(deps)
    result = await executor.run_stage(issue, issue.stage, deps.config)

    if result.passed:
        next_stage = issue.next_stage()
        if next_stage is None:
            await deps.db.update_issue_status(issue_id, IssueStatus.DONE, Stage.SHIP)
            update_issue_status(issue.filepath, IssueStatus.DONE, Stage.SHIP)
        else:
            await deps.db.update_issue_status(issue_id, IssueStatus.IN_PROGRESS, next_stage)
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
async def get_historical_events(request: Request, issue_id: str, deps: Deps = Depends(get_deps)):
    events = await deps.db.get_agent_events(issue_id)
    return events


@router.get("/issues/{issue_id}/events/stream")
async def stream_events(request: Request, issue_id: str, deps: Deps = Depends(get_deps)):
    from sse_starlette.sse import EventSourceResponse

    event_manager = _get_event_manager(deps)

    async def event_generator():
        async for event in event_manager.subscribe(issue_id):
            yield {
                "event": event.event_type,
                "data": json.dumps({"content": event.content, "metadata": event.metadata}),
            }
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(event_generator())


@router.get("/metrics")
async def get_metrics(request: Request, deps: Deps = Depends(get_deps)):
    issues = await deps.db.list_issues()
    total = len(issues)
    by_status: dict[str, int] = {}
    for issue in issues:
        s = issue["status"]
        by_status[s] = by_status.get(s, 0) + 1

    all_results = []
    for issue in issues:
        results = await deps.db.get_stage_results(issue["id"])
        all_results.extend(results)

    stage_attempts: dict[str, list[bool]] = {}
    for r in all_results:
        stage_attempts.setdefault(r["stage"], []).append(r["passed"])

    success_rates = {
        stage: sum(1 for p in passes if p) / len(passes) for stage, passes in stage_attempts.items()
    }

    all_iterations = []
    for issue in issues:
        iters = await deps.db.get_harness_iterations(issue["id"])
        all_iterations.extend(iters)

    total_retries = len(all_iterations)
    retries_by_stage: dict[str, int] = {}
    for it in all_iterations:
        retries_by_stage[it["stage"]] = retries_by_stage.get(it["stage"], 0) + 1

    metrics = PipelineMetrics(
        total_issues=total,
        issues_by_status=by_status,
        stage_success_rates=success_rates,
        avg_stage_duration_ms={},
        total_retries=total_retries,
        retries_by_stage=retries_by_stage,
        recent_events=[],
    )
    return metrics.model_dump()


@router.get("/metrics/dashboard", response_class=HTMLResponse)
async def metrics_dashboard(request: Request, deps: Deps = Depends(get_deps)):
    metrics_resp = await get_metrics(request, deps)
    return get_templates().TemplateResponse(
        request,
        "metrics.html",
        {"metrics": metrics_resp},
    )
