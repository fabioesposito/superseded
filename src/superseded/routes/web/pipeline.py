from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse

from superseded.models import Stage
from superseded.routes import get_templates
from superseded.routes.deps import (
    Deps,
    _find_issue,
    _get_event_manager,
    _render_issue_detail_oob,
    _render_running_indicator,
    _run_stage_background,
    _running,
    get_deps,
)
from superseded.tickets.reader import list_issues
from superseded.validation import InvalidInputError, validate_issue_id

router = APIRouter(prefix="/pipeline")


@router.post("/issues/{issue_id}/advance", response_class=HTMLResponse)
async def advance_issue(
    request: Request,
    issue_id: str,
    background_tasks: BackgroundTasks,
    deps: Deps = Depends(get_deps),
):
    try:
        issue_id = validate_issue_id(issue_id)
    except InvalidInputError:
        return HTMLResponse(content="")
    issue = _find_issue(deps, issue_id)
    if issue is None:
        return HTMLResponse(content="")

    if issue_id in _running:
        return _render_running_indicator(request, issue.stage.value)

    _running.add(issue_id)
    background_tasks.add_task(_run_stage_background, deps, issue_id, issue.stage)

    templates = get_templates()
    running_html = templates.TemplateResponse(
        request, "_running.html", {"stage_name": issue.stage.value}
    ).body.decode()
    poll_html = (
        f'<div id="issue-detail-content" hx-get="/pipeline/issues/{issue_id}/status" '
        f'hx-trigger="every 3s" hx-swap="innerHTML" hx-target="#issue-detail-content">'
        f"{running_html}</div>"
    )
    return HTMLResponse(content=poll_html)


@router.post("/issues/{issue_id}/retry", response_class=HTMLResponse)
async def retry_issue(
    request: Request,
    issue_id: str,
    background_tasks: BackgroundTasks,
    deps: Deps = Depends(get_deps),
):
    try:
        issue_id = validate_issue_id(issue_id)
    except InvalidInputError:
        return HTMLResponse(content="")
    issue = _find_issue(deps, issue_id)
    if issue is None:
        return HTMLResponse(content="")

    if issue_id in _running:
        return _render_running_indicator(request, issue.stage.value)

    _running.add(issue_id)
    background_tasks.add_task(_run_stage_background, deps, issue_id, issue.stage)

    templates = get_templates()
    running_html = templates.TemplateResponse(
        request, "_running.html", {"stage_name": issue.stage.value}
    ).body.decode()
    poll_html = (
        f'<div id="issue-detail-content" hx-get="/pipeline/issues/{issue_id}/status" '
        f'hx-trigger="every 3s" hx-swap="innerHTML" hx-target="#issue-detail-content">'
        f"{running_html}</div>"
    )
    return HTMLResponse(content=poll_html)


@router.get("/issues/{issue_id}/status", response_class=HTMLResponse)
async def issue_pipeline_status(request: Request, issue_id: str, deps: Deps = Depends(get_deps)):
    try:
        issue_id = validate_issue_id(issue_id)
    except InvalidInputError:
        return HTMLResponse(content="")

    if issue_id in _running:
        issue = _find_issue(deps, issue_id)
        if issue:
            return _render_running_indicator(request, issue.stage.value)
        return HTMLResponse(content="")

    issue = _find_issue(deps, issue_id)
    if issue is None:
        return HTMLResponse(content="")
    stage_results = await deps.db.get_stage_results(issue_id)
    harness_iterations = await deps.db.get_harness_iterations(issue_id)
    passed_stages = [r["stage"] for r in stage_results if r.get("passed")]

    return _render_issue_detail_oob(
        request, issue, stage_results, harness_iterations, passed_stages
    )


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


@router.get("/metrics", response_class=HTMLResponse)
async def metrics_dashboard(request: Request, deps: Deps = Depends(get_deps)):
    from superseded.routes.api.pipeline import _compute_metrics

    metrics_data = await _compute_metrics(deps)
    return get_templates().TemplateResponse(
        request,
        "metrics.html",
        {"metrics": metrics_data},
    )


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
