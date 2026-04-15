from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Request
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
api_router = APIRouter(prefix="/api/pipeline")

# Track which issues are currently running a stage
_running: set[str] = set()


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

    durations: dict[str, str] = {}
    for r in stage_results:
        sa = r.get("started_at")
        fa = r.get("finished_at")
        if sa and fa:
            started = datetime.datetime.fromisoformat(str(sa)) if isinstance(sa, str) else sa
            finished = datetime.datetime.fromisoformat(str(fa)) if isinstance(fa, str) else fa
            dur = (finished - started).total_seconds()
            if dur >= 60:
                durations[r["stage"]] = f"{int(dur // 60)}m {int(dur % 60)}s"
            else:
                durations[r["stage"]] = f"{int(dur)}s"

    context = {
        "issue": issue,
        "stage_results": stage_results,
        "harness_iterations": harness_iterations,
        "stage_order": [s.value for s in Stage],
        "passed_stages": passed_stages,
        "durations": durations,
    }
    progress = templates.TemplateResponse(request, "_progress.html", context)
    actions = templates.TemplateResponse(request, "_actions.html", context)
    results = templates.TemplateResponse(request, "_results.html", context)

    body = progress.body.decode() + actions.body.decode() + results.body.decode()
    return HTMLResponse(content=body)


def _render_running_indicator(request: Request, stage_name: str) -> HTMLResponse:
    templates = get_templates()
    return templates.TemplateResponse(request, "_running.html", {"stage_name": stage_name})


async def _run_stage_background(deps: Deps, issue_id: str, stage: Stage) -> None:
    """Run a stage in the background, updating DB on completion."""
    try:
        issue = _find_issue(deps, issue_id)
        if issue is None:
            return

        executor = _get_executor(deps)
        result = await executor.run_stage(issue, stage, deps.config)

        if result.passed:
            next_stage = issue.next_stage()
            if next_stage is None:
                await deps.db.update_issue_status(issue_id, IssueStatus.DONE, Stage.SHIP)
                update_issue_status(issue.filepath, IssueStatus.DONE, Stage.SHIP)
                if (
                    executor.notification_service
                    and executor.notification_service.enabled
                    and executor.notification_service.topic
                ):
                    await executor.notification_service.notify(
                        title=f"{issue_id}: SHIPPED!",
                        message=f"Pipeline complete for {issue_id}",
                        priority="default",
                        tags=["rocket"],
                        click_url=f"http://localhost:8000/issues/{issue_id}",
                    )
            else:
                await deps.db.update_issue_status(issue_id, IssueStatus.IN_PROGRESS, next_stage)
                update_issue_status(issue.filepath, IssueStatus.IN_PROGRESS, next_stage)
        else:
            await deps.db.update_issue_status(issue_id, IssueStatus.PAUSED, issue.stage)
            if (
                executor.notification_service
                and executor.notification_service.enabled
                and executor.notification_service.topic
            ):
                await executor.notification_service.notify(
                    title=f"{issue_id}: PAUSED",
                    message=f"Pipeline paused at {issue.stage.value}",
                    priority="high",
                    tags=["warning"],
                    click_url=f"http://localhost:8000/issues/{issue_id}",
                )
    except Exception:
        logger.exception("Background stage run failed for %s", issue_id)
        with contextlib.suppress(Exception):
            await deps.db.update_issue_status(issue_id, IssueStatus.PAUSED, stage)
    finally:
        _running.discard(issue_id)


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

    # If already running, return running indicator
    if issue_id in _running:
        return _render_running_indicator(request, issue.stage.value)

    # Mark as running and start background task
    _running.add(issue_id)
    background_tasks.add_task(_run_stage_background, deps, issue_id, issue.stage)

    # Return immediately with running indicator + auto-refresh trigger
    templates = get_templates()
    running_html = templates.TemplateResponse(
        request, "_running.html", {"stage_name": issue.stage.value}
    ).body.decode()
    # Add HTMX trigger that polls for completion
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
    """Poll endpoint — returns full issue-detail-content when stage completes."""
    try:
        issue_id = validate_issue_id(issue_id)
    except InvalidInputError:
        return HTMLResponse(content="")

    # If still running, keep the running indicator
    if issue_id in _running:
        issue = _find_issue(deps, issue_id)
        if issue:
            return _render_running_indicator(request, issue.stage.value)
        return HTMLResponse(content="")

    # Stage completed — return full updated content
    issue = _find_issue(deps, issue_id)
    if issue is None:
        return HTMLResponse(content="")
    stage_results = await deps.db.get_stage_results(issue_id)
    harness_iterations = await deps.db.get_harness_iterations(issue_id)
    passed_stages = [r["stage"] for r in stage_results if r.get("passed")]

    # Return full content WITHOUT poll trigger (stop polling)
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


@api_router.get("/metrics")
async def get_metrics(deps: Deps = Depends(get_deps)):
    return await _compute_metrics(deps)


async def _compute_metrics(deps: Deps) -> dict:
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

    stage_durations: dict[str, list[float]] = {}
    for r in all_results:
        started = r.get("started_at")
        finished = r.get("finished_at")
        if started and finished:
            try:
                dt_start = datetime.datetime.fromisoformat(started)
                dt_end = datetime.datetime.fromisoformat(finished)
                dur = (dt_end - dt_start).total_seconds() * 1000
                if dur > 0:
                    stage_durations.setdefault(r["stage"], []).append(dur)
            except (ValueError, TypeError):
                pass

    avg_stage_duration_ms = {
        stage: sum(durs) / len(durs) for stage, durs in stage_durations.items()
    }

    return PipelineMetrics(
        total_issues=total,
        issues_by_status=by_status,
        stage_success_rates=success_rates,
        avg_stage_duration_ms=avg_stage_duration_ms,
        total_retries=total_retries,
        retries_by_stage=retries_by_stage,
        recent_events=[],
    ).model_dump()


@router.get("/metrics", response_class=HTMLResponse)
async def metrics_dashboard(request: Request, deps: Deps = Depends(get_deps)):
    metrics_data = await _compute_metrics(deps)
    return get_templates().TemplateResponse(
        request,
        "metrics.html",
        {"metrics": metrics_data},
    )
