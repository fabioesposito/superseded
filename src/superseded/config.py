from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel


class PassConfig(BaseModel):
    security: bool = True
    correctness: bool = True
    performance: bool = True
    style: bool = True
    architecture: bool = True


class Config(BaseModel):
    provider: str = "deepseek"
    model: str | None = None
    reasoning_effort: Literal["low", "high", "max"] = "max"
    passes: PassConfig = PassConfig()
    post_to_pr: bool = False
    format: str = "table"
    log_format: str = "text"
    log_level: str = "WARNING"
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
    verify: bool = True

    def is_pass_enabled(self, name: str) -> bool:
        return getattr(self.passes, name, False)


_LEGACY_CLI_AGENTS = {"claude-code", "opencode", "codex"}


def load_config(path: Path | None = None) -> Config:
    if path is None:
        path = Path(".superseded.yaml")
    if not path.exists():
        return Config()
    data = yaml.safe_load(path.read_text()) or {}

    # Backward-compat: hard-error on legacy `agent: <cli-agent>` values.
    if "agent" in data:
        legacy_value = data["agent"]
        if isinstance(legacy_value, str) and legacy_value in _LEGACY_CLI_AGENTS:
            raise ValueError(
                "CLI agents were removed in v0.6.0. Set 'provider: deepseek' and "
                "$SUPERSEDED_DEEPSEEK_API_KEY. See MIGRATION.md."
            )
        # Unknown value — treat as `provider:` and warn.
        warnings.warn(
            "`agent:` in .superseded.yaml is renamed to `provider:`.",
            DeprecationWarning,
            stacklevel=2,
        )
        data.setdefault("provider", data.pop("agent"))

    # Backward-compat: silently drop `sandbox:` (no longer used).
    if "sandbox" in data:
        warnings.warn(
            "`sandbox:` in .superseded.yaml is no longer used (direct-API path has no subprocess to isolate).",
            DeprecationWarning,
            stacklevel=2,
        )
        data.pop("sandbox")

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
