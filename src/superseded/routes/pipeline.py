from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from superseded.config import SupersededConfig
from superseded.db import Database
from superseded.models import IssueStatus, Stage
from superseded.tickets.reader import list_issues
from superseded.tickets.writer import update_issue_status

router = APIRouter(prefix="/pipeline")

_config: SupersededConfig | None = None
_db: Database | None = None


def set_deps(config: SupersededConfig, db: Database) -> None:
    global _config, _db
    _config = config
    _db = db


@router.post("/issues/{issue_id}/advance")
async def advance_issue(request: Request, issue_id: str):
    assert _config and _db
    issues_dir = str(Path(_config.repo_path) / _config.issues_dir)
    issues = [i for i in list_issues(issues_dir) if i.id == issue_id]
    if not issues:
        return RedirectResponse(url="/", status_code=303)

    issue = issues[0]
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
    await _db.update_issue_status(issue_id, IssueStatus.IN_PROGRESS, issue.stage)
    update_issue_status(issue.filepath, IssueStatus.IN_PROGRESS, issue.stage)

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
