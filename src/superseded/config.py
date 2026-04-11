from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class SupersededConfig(BaseModel):
    default_agent: str = "claude-code"
    stage_timeout_seconds: int = 600
    repo_path: str = ""
    port: int = 8000
    host: str = "127.0.0.1"
    db_path: str = ".superseded/state.db"
    issues_dir: str = ".superseded/issues"
    artifacts_dir: str = ".superseded/artifacts"


def load_config(repo_path: Path) -> SupersededConfig:
    config_file = repo_path / ".superseded" / "config.yaml"
    overrides: dict = {}
    if config_file.exists():
        with open(config_file) as f:
            overrides = yaml.safe_load(f) or {}
    overrides.setdefault("repo_path", str(repo_path))
    return SupersededConfig(**overrides)
