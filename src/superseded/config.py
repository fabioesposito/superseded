from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class StageAgentConfig(BaseModel):
    cli: str = "opencode"
    model: str = ""
    sandbox: Literal["host", "docker"] = "host"
    require_approval: bool = False
    rtk: bool = False


class RepoEntry(BaseModel):
    path: str
    git_url: str = ""
    branch: str = ""


class NotificationsConfig(BaseModel):
    enabled: bool = False
    ntfy_topic: str = ""


class SupersededConfig(BaseModel):
    default_agent: str = "opencode"
    stage_timeout_seconds: int = 600
    repo_path: str = ""
    repos: dict[str, RepoEntry] = Field(default_factory=dict)
    port: int = 8000
    # host: str = "127.0.0.1"
    host: str = "0.0.0.0"
    db_path: str = ".superseded/state.db"
    issues_dir: str = ".superseded/issues"
    artifacts_dir: str = ".superseded/artifacts"
    api_key: str = ""
    github_token: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    opencode_api_key: str = ""
    default_model: str = ""
    rtk: bool = False
    base_url: str = ""
    stages: dict[str, StageAgentConfig] = Field(default_factory=dict)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)


def load_config(repo_path: Path) -> SupersededConfig:
    config_file = repo_path / ".superseded" / "config.yaml"
    overrides: dict = {}
    if config_file.exists():
        with open(config_file) as f:
            overrides = yaml.safe_load(f) or {}
    overrides.setdefault("repo_path", str(repo_path))
    for env_key, config_key in [
        ("SUPERSEDED_API_KEY", "api_key"),
        ("GITHUB_TOKEN", "github_token"),
        ("OPENAI_API_KEY", "openai_api_key"),
        ("ANTHROPIC_API_KEY", "anthropic_api_key"),
        ("OPENCODE_API_KEY", "opencode_api_key"),
    ]:
        val = os.environ.get(env_key, "")
        if val:
            overrides[config_key] = val
    config = SupersededConfig(**overrides)
    if not config.base_url:
        config.base_url = f"http://{config.host}:{config.port}"
    return config


def save_config(config: SupersededConfig, repo_path: Path) -> None:
    config_file = repo_path / ".superseded" / "config.yaml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(exclude={"repo_path"})
    defaults = SupersededConfig().model_dump()
    data = {k: v for k, v in data.items() if v != defaults.get(k)}
    with open(config_file, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


VALID_AGENTS = {"opencode", "claude-code", "codex", "docker"}


def validate_config(config: SupersededConfig) -> list[str]:
    """Return list of validation error messages. Empty list means valid."""
    errors = []
    if not config.default_agent:
        errors.append(
            "default_agent is required. Set it to 'opencode', 'claude-code', 'codex', or 'docker'."
        )
    elif config.default_agent not in VALID_AGENTS:
        errors.append(
            f"Unknown default_agent: '{config.default_agent}'. "
            f"Valid agents: {', '.join(sorted(VALID_AGENTS))}."
        )
    if config.stage_timeout_seconds < 0:
        errors.append(
            f"stage_timeout_seconds must be non-negative, got {config.stage_timeout_seconds}."
        )
    if not (1 <= config.port <= 65535):
        errors.append(f"port must be 1-65535, got {config.port}.")
    return errors
