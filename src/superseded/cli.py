from __future__ import annotations

from pathlib import Path

import yaml

RULES_TEMPLATE = """\
# Project Rules

Rules injected into every agent prompt. Keep these short and non-negotiable.

## Code Style
- Use type hints on all function signatures
- No comments unless explicitly requested

## Testing
- Write tests for new functionality
- Run existing tests before committing

## Security
- Never commit secrets or API keys
- Validate all user input
"""

EXAMPLE_TICKET = """\
---
title: Example ticket
status: new
stage: spec
created: 2026-01-01
---

Describe what you want to build here. The pipeline will take it through
spec → plan → build → verify → review → ship.
"""


def init_command(repo_path: Path) -> None:
    """Scaffold .superseded/ directory with config, rules, and example ticket."""
    superseded_dir = repo_path / ".superseded"
    superseded_dir.mkdir(parents=True, exist_ok=True)

    config_file = superseded_dir / "config.yaml"
    if not config_file.exists():
        default_config = {"default_agent": "opencode", "port": 8000}
        with open(config_file, "w") as f:
            yaml.dump(default_config, f, default_flow_style=False, sort_keys=False)

    rules_file = superseded_dir / "rules.md"
    if not rules_file.exists():
        rules_file.write_text(RULES_TEMPLATE)

    issues_dir = superseded_dir / "issues"
    issues_dir.mkdir(exist_ok=True)

    existing_tickets = list(issues_dir.glob("*.md"))
    if not existing_tickets:
        (issues_dir / "SUP-001-example.md").write_text(EXAMPLE_TICKET)
