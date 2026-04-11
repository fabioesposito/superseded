from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from superseded.models import Stage
from superseded.routes.deps import Deps, get_deps
from superseded.tickets.reader import list_issues

router = APIRouter()

_templates_dir = Path(__file__).parent.parent.parent.parent / "templates"
_templates = Jinja2Templates(directory=str(_templates_dir))


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, deps: Deps = Depends(get_deps)):
    issues_dir = str(Path(deps.config.repo_path) / deps.config.issues_dir)
    issues = list_issues(issues_dir)
    stage_names = [s.value for s in Stage]
    return _templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "issues": issues,
            "stage_names": stage_names,
        },
    )
