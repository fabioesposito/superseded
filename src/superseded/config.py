from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class StageAgentConfig(BaseModel):
    cli: str = "opencode"
    model: str = "opencode-go/kimi-k2.5"


class RepoEntry(BaseModel):
    path: str
    git_url: str = ""
    branch: str = ""


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
    max_retries: int = 3
    retryable_stages: list[str] = Field(default_factory=lambda: ["build", "verify", "review"])
    api_key: str = ""
    default_model: str = "opencode-go/kimi-k2.5"
    stages: dict[str, StageAgentConfig] = Field(default_factory=dict)


def load_config(repo_path: Path) -> SupersededConfig:
    config_file = repo_path / ".superseded" / "config.yaml"
    overrides: dict = {}
    if config_file.exists():
        with open(config_file) as f:
            overrides = yaml.safe_load(f) or {}
    overrides.setdefault("repo_path", str(repo_path))
    env_api_key = os.environ.get("SUPERSEDED_API_KEY", "")
    if env_api_key:
        overrides["api_key"] = env_api_key
    return SupersededConfig(**overrides)


def save_config(config: SupersededConfig, repo_path: Path) -> None:
    config_file = repo_path / ".superseded" / "config.yaml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(exclude={"repo_path"})
    defaults = SupersededConfig().model_dump()
    data = {k: v for k, v in data.items() if v != defaults.get(k)}
    with open(config_file, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
