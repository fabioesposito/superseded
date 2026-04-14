from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from superseded.models import Stage
from superseded.routes import _csrf_token_for_request, get_templates
from superseded.routes.deps import Deps, get_deps
from superseded.tickets.reader import list_issues

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, deps: Deps = Depends(get_deps)):
    issues_dir = str(Path(deps.config.repo_path) / deps.config.issues_dir)
    issues = list_issues(issues_dir)
    stage_names = [s.value for s in Stage]
    response = get_templates().TemplateResponse(
        request,
        "dashboard.html",
        {
            "issues": issues,
            "stage_names": stage_names,
        },
    )
    if "csrf_token" not in request.cookies:
        token = _csrf_token_for_request(request)
        response.set_cookie("csrf_token", token, httponly=False, samesite="lax")
    return response
