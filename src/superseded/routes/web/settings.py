from __future__ import annotations

import logging
import shutil
from contextlib import suppress
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from superseded.config import RepoEntry, StageAgentConfig, SupersededConfig, save_config
from superseded.routes import _csrf_token_for_request, get_templates
from superseded.routes.service import Deps, get_deps, get_form_data
from superseded.validation import (
    InvalidInputError,
    validate_git_url,
    validate_repo_path,
)

logger = logging.getLogger(__name__)

router = APIRouter()


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
            "notifications": deps.config.notifications,
            "host": deps.config.host,
            "port": deps.config.port,
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
    form = await get_form_data(request)
    name = str(form.get("name", "")).strip()
    git_url = str(form.get("git_url", "")).strip()
    path = str(form.get("path", "")).strip()
    branch = str(form.get("branch", "")).strip()

    config = deps.config
    if not path:
        return get_templates().TemplateResponse(
            request,
            "_repos_table.html",
            {
                "repos": config.repos,
                "error": "Local path is required",
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
    form = await get_form_data(request)
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
    form = await get_form_data(request)
    config = deps.config
    stages_data = {
        "spec": StageAgentConfig(
            cli=str(form.get("spec_cli", "opencode")),
            model=str(form.get("spec_model", "")),
            sandbox=str(form.get("spec_sandbox", "host")),
            require_approval=bool(form.get("spec_approval")),
        ),
        "plan": StageAgentConfig(
            cli=str(form.get("plan_cli", "opencode")),
            model=str(form.get("plan_model", "")),
            sandbox=str(form.get("plan_sandbox", "host")),
            require_approval=bool(form.get("plan_approval")),
        ),
        "build": StageAgentConfig(
            cli=str(form.get("build_cli", "opencode")),
            model=str(form.get("build_model", "")),
            sandbox=str(form.get("build_sandbox", "host")),
            require_approval=bool(form.get("build_approval")),
        ),
        "verify": StageAgentConfig(
            cli=str(form.get("verify_cli", "opencode")),
            model=str(form.get("verify_model", "")),
            sandbox=str(form.get("verify_sandbox", "host")),
            require_approval=bool(form.get("verify_approval")),
        ),
        "review": StageAgentConfig(
            cli=str(form.get("review_cli", "opencode")),
            model=str(form.get("review_model", "")),
            sandbox=str(form.get("review_sandbox", "host")),
            require_approval=bool(form.get("review_approval")),
        ),
        "ship": StageAgentConfig(
            cli=str(form.get("ship_cli", "opencode")),
            model=str(form.get("ship_model", "")),
            sandbox=str(form.get("ship_sandbox", "host")),
            require_approval=bool(form.get("ship_approval")),
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
    form = await get_form_data(request)
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


@router.post("/settings/notifications", response_class=HTMLResponse)
async def update_notifications(request: Request, deps: Deps = Depends(get_deps)):
    form = await get_form_data(request)
    config = deps.config
    enabled = bool(form.get("enabled"))
    ntfy_topic = str(form.get("ntfy_topic", "")).strip()
    config.notifications.enabled = enabled
    config.notifications.ntfy_topic = ntfy_topic
    save_config(config, Path(config.repo_path))
    _reload_pipeline(request.app, config)
    return get_templates().TemplateResponse(
        request,
        "_notifications_field.html",
        {"notifications": config.notifications, "success": True},
    )


@router.post("/settings/server", response_class=HTMLResponse)
async def update_server_settings(request: Request, deps: Deps = Depends(get_deps)):
    form = await get_form_data(request)
    config = deps.config
    config.host = str(form.get("host", config.host)).strip()
    port_str = str(form.get("port", config.port)).strip()
    if port_str:
        with suppress(ValueError, TypeError):
            config.port = int(port_str)
    save_config(config, Path(config.repo_path))
    _reload_pipeline(request.app, config)
    return get_templates().TemplateResponse(
        request,
        "_server_settings_field.html",
        {"host": config.host, "port": config.port, "success": True},
    )


@router.get("/settings/rules", response_class=HTMLResponse)
async def get_rules_editor(request: Request, deps: Deps = Depends(get_deps)):
    rules_path = Path(deps.config.repo_path) / ".superseded" / "rules.md"
    rules_content = ""
    if rules_path.exists():
        rules_content = rules_path.read_text()
    return get_templates().TemplateResponse(
        request,
        "_rules_field.html",
        {"rules": rules_content},
    )


@router.post("/settings/rules", response_class=HTMLResponse)
async def save_rules(request: Request, deps: Deps = Depends(get_deps)):
    form = await get_form_data(request)
    rules_content = str(form.get("rules", ""))
    rules_path = Path(deps.config.repo_path) / ".superseded" / "rules.md"
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(rules_content)
    return get_templates().TemplateResponse(
        request,
        "_rules_field.html",
        {"rules": rules_content, "success": True},
    )


def _detect_agents() -> list[dict[str, str]]:
    agents = [
        {"name": "claude-code", "binary": "claude", "description": "Anthropic Claude Code"},
        {"name": "opencode", "binary": "opencode", "description": "OpenCode CLI"},
        {"name": "codex", "binary": "codex", "description": "OpenAI Codex CLI"},
    ]
    results = []
    for agent in agents:
        found = shutil.which(agent["binary"])
        results.append(
            {
                **agent,
                "available": found is not None,
                "path": found or "not found",
            }
        )
    return results


def _detect_api_keys(config: SupersededConfig) -> list[dict[str, str | bool]]:
    keys = [
        {
            "name": "ANTHROPIC_API_KEY",
            "configured": bool(config.anthropic_api_key),
            "agent": "claude-code",
        },
        {"name": "OPENAI_API_KEY", "configured": bool(config.openai_api_key), "agent": "codex"},
        {
            "name": "OPENCODE_API_KEY",
            "configured": bool(config.opencode_api_key),
            "agent": "opencode",
        },
        {
            "name": "GITHUB_TOKEN",
            "configured": bool(config.github_token),
            "agent": "ship (PR creation)",
        },
    ]
    return keys


@router.get("/settings/setup", response_class=HTMLResponse)
async def setup_wizard(request: Request, deps: Deps = Depends(get_deps)):
    agents = _detect_agents()
    api_keys = _detect_api_keys(deps.config)
    return get_templates().TemplateResponse(
        request,
        "_setup_wizard.html",
        {"agents": agents, "api_keys": api_keys},
    )


def _reload_pipeline(app, config: SupersededConfig) -> None:
    from superseded.main import _build_pipeline_state

    app.state.config = config
    pipeline = _build_pipeline_state(config, app.state.db)
    app.state.pipeline = pipeline
