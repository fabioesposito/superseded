from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from superseded.models import Stage
from superseded.routes import _csrf_token_for_request, get_templates
from superseded.routes.deps import Deps, get_deps
from superseded.routes.service import get_form_data
from superseded.tickets.reader import list_issues

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, stage: str | None = None, deps: Deps = Depends(get_deps)):
    issues_dir = str(Path(deps.config.repo_path) / deps.config.issues_dir)
    all_issues = list_issues(issues_dir)
    stage_names = [s.value for s in Stage]
    if stage and stage in stage_names:
        filtered_issues = [i for i in all_issues if i.stage.value == stage]
    else:
        filtered_issues = all_issues
        stage = None
    response = get_templates().TemplateResponse(
        request,
        "dashboard.html",
        {
            "issues": filtered_issues,
            "all_issues": all_issues,
            "stage_names": stage_names,
            "active_stage": stage,
        },
    )
    if "csrf_token" not in request.cookies:
        token = _csrf_token_for_request(request)
        response.set_cookie("csrf_token", token, httponly=False, samesite="lax")
    return response


@router.post("/bulk/retry", response_class=HTMLResponse)
async def bulk_retry(request: Request, deps: Deps = Depends(get_deps)):
    form = await get_form_data(request)
    issue_ids = [v for k, v in form.items() if k.startswith("issue_")]
    if not issue_ids:
        return RedirectResponse(url="/", status_code=303)

    issues_dir = str(Path(deps.config.repo_path) / deps.config.issues_dir)
    all_issues = list_issues(issues_dir)

    if deps.pipeline is None:
        return RedirectResponse(url="/", status_code=303)

    executor = deps.pipeline.executor
    for issue_id in issue_ids:
        matching = [i for i in all_issues if i.id == issue_id]
        if matching and matching[0].status.value == "paused":
            issue = matching[0]
            await executor.run_stage(issue, issue.stage, deps.config)

    return RedirectResponse(url="/", status_code=303)
