from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

SKILL_NAME = "superseded"
SKILL_AGENTS: tuple[str, ...] = ("claude-code", "opencode", "codex")

# agent name -> home-relative personal skills root (final segment added by skill_dir_for)
_AGENT_SKILLS_ROOT: dict[str, tuple[str, ...]] = {
    "claude-code": (".claude", "skills"),
    "opencode": (".config", "opencode", "skills"),
    "codex": (".agents", "skills"),
}

_SKILL_MD = """\
---
name: superseded
description: Use when the user asks to review a PR, diff, or code changes, or mentions "superseded" or "code review". superseded is an installed CLI that runs multi-pass AI code review — invoke it directly instead of reviewing by hand or probing for tools.
---

# Superseded (AI code review)

`superseded` is a CLI on PATH that runs 5 parallel AI review passes (security, correctness, performance, style, architecture) and merges/dedupes findings. **It is installed and available. Do not probe PATH, run `which`, or "check what's installed" — run it directly.**

## When to use
- User asks to "review this PR / diff / changes / code"
- User mentions "superseded"
- You're about to do a manual line-by-line review of a diff/PR

## How to invoke
```bash
superseded review --pr <N>                  # GitHub PR (agent/model from .superseded.yaml)
superseded review --diff HEAD~3..HEAD       # local diff range
superseded review --pr <N> --format json    # parseable findings (use this)
superseded review src/auth.py               # specific files
```
Agent/model default from `.superseded.yaml` (run `superseded init` if missing); override with `--agent`/`--model`. Progress goes to stderr; findings go to stdout.

## Output (`--format json`)
`{"findings": [...], "warnings": [...]}`. Each finding: `severity` (`critical`|`important`|`suggestion`|`nit`), `file`, `line`, `end_line`, `title`, `description`, `suggestion`, `pass_name`, `confidence`, `reasoning`. Summarize grouped by severity; cite `file:line`.

## Rules
- **Run `superseded review` for diff/PR code-review requests — do not write an ad-hoc manual review.**
- **Do not verify superseded is installed.** It is. (No `which`, no "let me check the environment".)
- On non-zero exit: `3` = partial success (some passes skipped, findings still produced); anything else = real error, surface it.
- Prefer `--pr <N>` for GitHub PRs, `--diff` for local ranges.
"""


def build_skill_text() -> str:
    """Return the canonical superseded SKILL.md content (deterministic)."""
    return _SKILL_MD


def skill_dir_for(agent_name: str) -> Path:
    """Resolve the personal skills dir for an agent.

    e.g. claude-code -> ~/.claude/skills/superseded
    """
    return Path.home().joinpath(*_AGENT_SKILLS_ROOT[agent_name], SKILL_NAME)


def install_skill(
    agents: list[str],
    *,
    force: bool = False,
    status: Callable[[str], None] | None = None,
) -> tuple[list[str], list[str]]:
    """Write SKILL.md to each selected agent's personal skills dir.

    Returns ``(written, skipped)``. A target is treated as written when it is
    absent, already content-identical, or present with ``force=True``. A target
    that exists with differing content and no ``force`` is skipped (returned in
    ``skipped``) and left untouched. Writes are atomic (temp file + os.replace).
    Never raises on conflict; reports nuance via optional ``status`` callbacks.
    """
    text = build_skill_text()
    written: list[str] = []
    skipped: list[str] = []
    for name in agents:
        target_dir = skill_dir_for(name)
        target_file = target_dir / "SKILL.md"
        if target_file.exists():
            if target_file.read_text() == text:
                written.append(name)
                if status:
                    status(f"{name}: already up to date -> {target_file}")
                continue
            if not force:
                skipped.append(name)
                if status:
                    status(f"{name}: skipped (differs, use --force) -> {target_file}")
                continue
        target_dir.mkdir(parents=True, exist_ok=True)
        tmp = target_file.with_name(target_file.name + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, target_file)
        written.append(name)
        if status:
            status(f"{name}: wrote {target_file}")
    return written, skipped
