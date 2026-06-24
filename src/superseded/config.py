from __future__ import annotations

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
    agent: str = "claude-code"
    model: str | None = None
    passes: PassConfig = PassConfig()
    post_to_pr: bool = False
    format: str = "table"
    memory: bool = True

    def is_pass_enabled(self, name: str) -> bool:
        return getattr(self.passes, name, False)


def load_config(path: Path | None = None) -> Config:
    if path is None:
        path = Path(".superseded.yaml")
    if not path.exists():
        return Config()
    data = yaml.safe_load(path.read_text()) or {}
    return Config(**data)
