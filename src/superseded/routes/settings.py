from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from superseded.config import RepoEntry, StageAgentConfig, SupersededConfig, save_config
from superseded.routes import _csrf_token_for_request, get_templates
from superseded.routes.deps import Deps, get_deps
from superseded.validation import (
    InvalidInputError,
    validate_directory_path,
    validate_git_url,
    validate_repo_path,
)

router = APIRouter()


async def _get_form_data(request: Request):
    """Get form data, checking request.state first (set by CSRF middleware)."""
    if hasattr(request.state, "form_data"):
        return request.state.form_data
    try:
        form = await request.form()
        return dict(form)
    except Exception:
        return {}


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, deps: Deps = Depends(get_deps)):
    repos = deps.config.repos
    stages = ["spec", "plan", "build", "verify", "review", "ship"]
    stage_agents = {}
    for stage in stages:
        stage_agents[stage] = deps.config.stages.get(stage, StageAgentConfig())
    response = get_templates().TemplateResponse(
        request,
        "settings.html",
        {
            "repos": repos,
            "stage_agents": stage_agents,
            "github_token": deps.config.github_token,
            "openai_api_key": deps.config.openai_api_key,
            "anthropic_api_key": deps.config.anthropic_api_key,
            "opencode_api_key": deps.config.opencode_api_key,
            "source_code_root": deps.config.source_code_root,
        },
    )
    if "csrf_token" not in request.cookies:
        token = _csrf_token_for_request(request)
        response.set_cookie("csrf_token", token, httponly=False, samesite="lax")
    return response


@router.post("/settings/repos", response_class=HTMLResponse)
async def add_repo(
    request: Request,
    deps: Deps = Depends(get_deps),
):
    form = await _get_form_data(request)
    name = str(form.get("name", "")).strip()
    git_url = str(form.get("git_url", "")).strip()
    path = str(form.get("path", "")).strip()
    branch = str(form.get("branch", "")).strip()

    config = deps.config
    if not path and config.source_code_root:
        path = f"{config.source_code_root.rstrip('/')}/{name}"
    if not path:
        return get_templates().TemplateResponse(
            request,
            "_repos_table.html",
            {
                "repos": config.repos,
                "error": "Local path is required (or set a source root in Settings)",
            },
            status_code=400,
        )
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


@router.post("/settings/token", response_class=HTMLResponse)
async def update_token(request: Request, deps: Deps = Depends(get_deps)):
    form = await _get_form_data(request)
    token = str(form.get("github_token", "")).strip()
    config = deps.config
    config.github_token = token
    save_config(config, Path(config.repo_path))
    _reload_pipeline(request.app, config)
    return get_templates().TemplateResponse(
        request,
        "_token_field.html",
        {"github_token": token, "success": True},
    )


@router.post("/settings/agents", response_class=HTMLResponse)
async def update_agents(
    request: Request,
    deps: Deps = Depends(get_deps),
):
    form = await _get_form_data(request)
    config = deps.config
    stages_data = {
        "spec": StageAgentConfig(
            cli=str(form.get("spec_cli", "opencode")),
            model=str(form.get("spec_model", "opencode-go/kimi-k2.5")),
        ),
        "plan": StageAgentConfig(
            cli=str(form.get("plan_cli", "opencode")),
            model=str(form.get("plan_model", "opencode-go/kimi-k2.5")),
        ),
        "build": StageAgentConfig(
            cli=str(form.get("build_cli", "opencode")),
            model=str(form.get("build_model", "opencode-go/kimi-k2.5")),
        ),
        "verify": StageAgentConfig(
            cli=str(form.get("verify_cli", "opencode")),
            model=str(form.get("verify_model", "opencode-go/kimi-k2.5")),
        ),
        "review": StageAgentConfig(
            cli=str(form.get("review_cli", "opencode")),
            model=str(form.get("review_model", "opencode-go/kimi-k2.5")),
        ),
        "ship": StageAgentConfig(
            cli=str(form.get("ship_cli", "opencode")),
            model=str(form.get("ship_model", "opencode-go/kimi-k2.5")),
        ),
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


def _mask_key(key: str) -> str:
    if not key or len(key) < 8:
        return "*" * len(key) if key else ""
    return key[:4] + "*" * (len(key) - 4)


@router.post("/settings/api-keys", response_class=HTMLResponse)
async def update_api_keys(request: Request, deps: Deps = Depends(get_deps)):
    form = await _get_form_data(request)
    config = deps.config
    config.openai_api_key = str(form.get("openai_api_key", "")).strip()
    config.anthropic_api_key = str(form.get("anthropic_api_key", "")).strip()
    config.opencode_api_key = str(form.get("opencode_api_key", "")).strip()
    save_config(config, Path(config.repo_path))
    _reload_pipeline(request.app, config)
    return get_templates().TemplateResponse(
        request,
        "_api_keys_field.html",
        {
            "openai_api_key": config.openai_api_key,
            "anthropic_api_key": config.anthropic_api_key,
            "opencode_api_key": config.opencode_api_key,
            "success": True,
        },
    )


@router.post("/settings/source-root", response_class=HTMLResponse)
async def update_source_root(request: Request, deps: Deps = Depends(get_deps)):
    form = await _get_form_data(request)
    raw_path = str(form.get("source_code_root", "")).strip()
    config = deps.config
    try:
        validated = validate_directory_path(raw_path) if raw_path else ""
    except InvalidInputError as e:
        return get_templates().TemplateResponse(
            request,
            "_source_root_field.html",
            {"source_code_root": config.source_code_root, "error": str(e)},
            status_code=400,
        )
    config.source_code_root = validated
    save_config(config, Path(config.repo_path))
    _reload_pipeline(request.app, config)
    return get_templates().TemplateResponse(
        request,
        "_source_root_field.html",
        {"source_code_root": validated, "success": True},
    )


def _reload_pipeline(app, config: SupersededConfig) -> None:
    from superseded.main import _build_pipeline_state

    app.state.config = config
    pipeline = _build_pipeline_state(config)
    pipeline.executor.db = app.state.db
    app.state.pipeline = pipeline
