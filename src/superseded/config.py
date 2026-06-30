from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel


class PassConfig(BaseModel):
    security: bool = True
    correctness: bool = True
    performance: bool = True
    style: bool = True
    architecture: bool = True


class Config(BaseModel):
    agent: str = "opencode"
    model: str | None = "deepseek-v4-pro"
    passes: PassConfig = PassConfig()
    post_to_pr: bool = False
    format: str = "table"
    memory: bool = True
    static_analysis: bool = True
    usage_retrieval: bool = True
    conventions: bool = True
    spec_retrieval: bool = True
    graph: bool = True
    progressive: bool = True
    learned_review: bool = True
    reflection_threshold: int = 5
    max_learned_rules: int = 5

    def is_pass_enabled(self, name: str) -> bool:
        return getattr(self.passes, name, False)


def load_config(path: Path | None = None) -> Config:
    if path is None:
        path = Path(".superseded.yaml")
    if not path.exists():
        return Config()
    data = yaml.safe_load(path.read_text()) or {}
    return Config(**data)


def write_config(config: Config, path: Path | None = None) -> None:
    """Atomically write `config` to `path` as YAML.

    `path` defaults to `.superseded.yaml` in the current working directory.
    Writes to a sibling temp file and replaces the target on success so a
    crash mid-write never leaves a half-written config.
    """
    if path is None:
        path = Path(".superseded.yaml")
    data = config.model_dump(mode="json")
    text = yaml.safe_dump(data, sort_keys=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
