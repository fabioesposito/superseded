from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from superseded.models import (
    IssueStatus,
    PipelineMetrics,
    Stage,
)
from superseded.routes import get_templates
from superseded.routes.deps import Deps, get_deps
from superseded.tickets.reader import list_issues
from superseded.tickets.writer import update_issue_status
from superseded.validation import InvalidInputError, validate_issue_id

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


def _render_issue_detail_oob(
    request: Request, issue, stage_results, harness_iterations, passed_stages
) -> HTMLResponse:
    templates = get_templates()
    context = {
        "issue": issue,
        "stage_results": stage_results,
        "harness_iterations": harness_iterations,
        "stage_order": [s.value for s in Stage],
        "passed_stages": passed_stages,
    }
    progress = templates.TemplateResponse(request, "_progress.html", context)
    actions = templates.TemplateResponse(request, "_actions.html", context)
    results = templates.TemplateResponse(request, "_results.html", context)

    body = progress.body.decode() + actions.body.decode() + results.body.decode()
    return HTMLResponse(content=body)


async def _run_and_advance(
    deps: Deps, issue_id: str, stage: Stage, request: Request
) -> HTMLResponse:
    issue = _find_issue(deps, issue_id)
    if issue is None:
        return HTMLResponse(content="")

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
    else:
        await deps.db.update_issue_status(issue_id, IssueStatus.PAUSED, issue.stage)

    issue = _find_issue(deps, issue_id)
    stage_results = await deps.db.get_stage_results(issue_id)
    harness_iterations = await deps.db.get_harness_iterations(issue_id)
    passed_stages = [r["stage"] for r in stage_results if r.get("passed")]

    return _render_issue_detail_oob(
        request, issue, stage_results, harness_iterations, passed_stages
    )


@router.post("/issues/{issue_id}/advance", response_class=HTMLResponse)
async def advance_issue(request: Request, issue_id: str, deps: Deps = Depends(get_deps)):
    try:
        issue_id = validate_issue_id(issue_id)
    except InvalidInputError:
        return HTMLResponse(content="")
    issue = _find_issue(deps, issue_id)
    if issue is None:
        return HTMLResponse(content="")
    return await _run_and_advance(deps, issue_id, issue.stage, request)


@router.post("/issues/{issue_id}/retry", response_class=HTMLResponse)
async def retry_issue(request: Request, issue_id: str, deps: Deps = Depends(get_deps)):
    try:
        issue_id = validate_issue_id(issue_id)
    except InvalidInputError:
        return HTMLResponse(content="")
    issue = _find_issue(deps, issue_id)
    if issue is None:
        return HTMLResponse(content="")
    return await _run_and_advance(deps, issue_id, issue.stage, request)


@router.get("/sse/dashboard")
async def dashboard_sse(request: Request, stage: str | None = None, deps: Deps = Depends(get_deps)):
    from sse_starlette.sse import EventSourceResponse

    templates = get_templates()

    async def event_generator():
        last_hash = None
        while True:
            if await request.is_disconnected():
                break
            issues_dir = str(Path(deps.config.repo_path) / deps.config.issues_dir)
            all_issues = list_issues(issues_dir)
            stage_names = [s.value for s in Stage]
            if stage and stage in stage_names:
                filtered = [i for i in all_issues if i.stage.value == stage]
            else:
                filtered = all_issues
            data = json.dumps([i.model_dump() for i in all_issues], default=str)
            current_hash = str(hash(data))
            if current_hash != last_hash:
                last_hash = current_hash
                ctx = {
                    "issues": filtered,
                    "all_issues": all_issues,
                    "stage_names": stage_names,
                    "active_stage": stage,
                }
                table_html = templates.TemplateResponse(
                    request, "_dashboard_table.html", ctx
                ).body.decode()
                counters_html = templates.TemplateResponse(
                    request, "_stage_counters.html", ctx
                ).body.decode()
                yield {
                    "event": "table",
                    "data": table_html,
                }
                yield {
                    "event": "counters",
                    "data": counters_html,
                }
            await asyncio.sleep(2)

    return EventSourceResponse(event_generator())


@router.get("/issues/{issue_id}/events")
async def get_historical_events(request: Request, issue_id: str, deps: Deps = Depends(get_deps)):
    try:
        issue_id = validate_issue_id(issue_id)
    except InvalidInputError:
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url="/", status_code=303)
    events = await deps.db.get_agent_events(issue_id)
    return events


@router.get("/issues/{issue_id}/events/stream")
async def stream_events(request: Request, issue_id: str, deps: Deps = Depends(get_deps)):
    from sse_starlette.sse import EventSourceResponse

    try:
        issue_id = validate_issue_id(issue_id)
    except InvalidInputError:
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url="/", status_code=303)

    event_manager = _get_event_manager(deps)
    event_manager.start(issue_id)

    async def event_generator():
        try:
            async for event in event_manager.subscribe(issue_id):
                yield {
                    "event": event.event_type,
                    "data": json.dumps({"content": event.content, "metadata": event.metadata}),
                }
        finally:
            event_manager.stop(issue_id)
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
