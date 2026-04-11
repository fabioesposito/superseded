from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from superseded.config import SupersededConfig
from superseded.db import Database
from superseded.models import Stage
from superseded.tickets.reader import list_issues

router = APIRouter()

_config: SupersededConfig | None = None
_db: Database | None = None

_templates_dir = Path(__file__).parent.parent.parent.parent / "templates"
_templates = Jinja2Templates(directory=str(_templates_dir))


def set_deps(config: SupersededConfig, db: Database) -> None:
    global _config, _db
    _config = config
    _db = db


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    assert _config
    issues_dir = str(Path(_config.repo_path) / _config.issues_dir)
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
