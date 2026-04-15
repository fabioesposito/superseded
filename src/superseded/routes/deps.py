from __future__ import annotations

import contextlib
import datetime
import logging
from dataclasses import dataclass
from pathlib import Path

from fastapi import BackgroundTasks, Request
from fastapi.responses import HTMLResponse

from superseded.config import SupersededConfig
from superseded.db import Database
from superseded.models import IssueStatus, Stage
from superseded.pipeline.events import PipelineEventManager
from superseded.pipeline.executor import StageExecutor
from superseded.routes import get_templates
from superseded.tickets.reader import list_issues
from superseded.tickets.writer import update_issue_status
from superseded.validation import InvalidInputError, validate_issue_id

logger = logging.getLogger(__name__)

_running: set[str] = set()


@dataclass
class PipelineState:
    executor: StageExecutor
    event_manager: PipelineEventManager


@dataclass
class Deps:
    config: SupersededConfig
    db: Database
    pipeline: PipelineState | None = None


async def get_deps(request: Request) -> Deps:
    return Deps(
        config=request.app.state.config,
        db=request.app.state.db,
        pipeline=getattr(request.app.state, "pipeline", None),
    )


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


async def _run_and_advance(
    deps: Deps, issue_id: str, stage: Stage, request: Request, background_tasks: BackgroundTasks
) -> HTMLResponse:
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
