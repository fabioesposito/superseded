from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from superseded.config import RepoEntry, SupersededConfig, save_config
from superseded.main import _build_pipeline_state
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


@router.post("/settings/repos", response_class=HTMLResponse)
async def add_repo(
    request: Request,
    deps: Deps = Depends(get_deps),
    name: str = Form(...),
    git_url: str = Form(""),
    path: str = Form(...),
    branch: str = Form(""),
):
    config = deps.config
    config.repos[name] = RepoEntry(path=path, git_url=git_url, branch=branch)
    save_config(config, Path(config.repo_path))
    _reload_pipeline(request.app, config)
    return get_templates().TemplateResponse(request, "_repos_table.html", {"repos": config.repos})


@router.delete("/settings/repos/{repo_name}", response_class=HTMLResponse)
async def delete_repo(
    request: Request,
    repo_name: str,
    deps: Deps = Depends(get_deps),
):
    config = deps.config
    config.repos.pop(repo_name, None)
    save_config(config, Path(config.repo_path))
    _reload_pipeline(request.app, config)
    return get_templates().TemplateResponse(request, "_repos_table.html", {"repos": config.repos})


def _reload_pipeline(app, config: SupersededConfig) -> None:
    app.state.config = config
    pipeline = _build_pipeline_state(config)
    pipeline.executor.db = app.state.db
    app.state.pipeline = pipeline
