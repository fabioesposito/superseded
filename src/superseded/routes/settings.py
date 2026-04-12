from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from superseded.routes import get_templates
from superseded.routes.deps import Deps, get_deps

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, deps: Deps = Depends(get_deps)):
    repos = deps.config.repos
    return get_templates().TemplateResponse(
        request,
        "settings.html",
        {
            "repos": repos,
        },
    )
