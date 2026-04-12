from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from superseded.config import RepoEntry, StageAgentConfig, SupersededConfig, save_config
from superseded.routes import get_templates
from superseded.routes.deps import Deps, get_deps
from superseded.validation import InvalidInputError, validate_git_url, validate_repo_path

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, deps: Deps = Depends(get_deps)):
    repos = deps.config.repos
    stages = ["spec", "plan", "build", "verify", "review", "ship"]
    stage_agents = {}
    for stage in stages:
        stage_agents[stage] = deps.config.stages.get(stage, StageAgentConfig())
    return get_templates().TemplateResponse(
        request,
        "settings.html",
        {
            "repos": repos,
            "stage_agents": stage_agents,
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
    try:
        if git_url.strip():
            git_url = validate_git_url(git_url)
        path = validate_repo_path(path)
    except InvalidInputError as e:
        return get_templates().TemplateResponse(
            request,
            "_repos_table.html",
            {"repos": config.repos, "error": str(e)},
            status_code=400,
        )
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


@router.post("/settings/agents", response_class=HTMLResponse)
async def update_agents(
    request: Request,
    deps: Deps = Depends(get_deps),
    spec_cli: str = Form("claude-code"),
    spec_model: str = Form(""),
    plan_cli: str = Form("claude-code"),
    plan_model: str = Form(""),
    build_cli: str = Form("claude-code"),
    build_model: str = Form(""),
    verify_cli: str = Form("claude-code"),
    verify_model: str = Form(""),
    review_cli: str = Form("claude-code"),
    review_model: str = Form(""),
    ship_cli: str = Form("claude-code"),
    ship_model: str = Form(""),
):
    config = deps.config
    stages_data = {
        "spec": StageAgentConfig(cli=spec_cli, model=spec_model),
        "plan": StageAgentConfig(cli=plan_cli, model=plan_model),
        "build": StageAgentConfig(cli=build_cli, model=build_model),
        "verify": StageAgentConfig(cli=verify_cli, model=verify_model),
        "review": StageAgentConfig(cli=review_cli, model=review_model),
        "ship": StageAgentConfig(cli=ship_cli, model=ship_model),
    }
    config.stages = stages_data
    save_config(config, Path(config.repo_path))
    _reload_pipeline(request.app, config)

    stage_agents = {k: v for k, v in stages_data.items()}
    return get_templates().TemplateResponse(
        request,
        "_agents_table.html",
        {"stage_agents": stage_agents},
    )


def _reload_pipeline(app, config: SupersededConfig) -> None:
    from superseded.main import _build_pipeline_state

    app.state.config = config
    pipeline = _build_pipeline_state(config)
    pipeline.executor.db = app.state.db
    app.state.pipeline = pipeline
