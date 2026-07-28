# `superseded skill` Command — Design

**Date:** 2026-07-28
**Status:** Approved (brainstormed)
**Scope:** Add a `skill` command group that installs (or prints) a `SKILL.md` into each supported AI agent's personal skill directory, so claude-code, opencode, and codex recognize `superseded` as an installed tool and invoke it directly for code review instead of probing PATH or falling back to ad-hoc manual review.

## Problem

When a user asks an AI agent (claude-code, opencode, codex) to review a PR via `superseded`, the agent frequently responds with variants of:

> "I don't recognize superseded or opencode as available tools in this environment yet, so let me check what's actually installed before running anything against the PR."

The agent then wastes turns probing PATH / running `which`, or abandons `superseded` entirely and writes its own inferior ad-hoc review. The root cause is that the agent has no awareness that `superseded` exists and is installed.

## Goals

- Let a user run `superseded skill install` once so that every supported AI agent, in any repo, thereafter knows `superseded` is installed and how to invoke it.
- Provide `superseded skill print` to emit the same `SKILL.md` to stdout — usable as a pasteable/GitHub prompt or committable to `.github/`.
- Keep the command non-interactive and scriptable, mirroring `superseded init`.
- Keep all generation/writing logic isolated in a pure, unit-testable module separate from click/IO.

## Non-goals

- Project-level (committed, repo-scoped) skill installation. Personal/user-global only. (A `--project` flag is a deliberate future extension, not in scope.)
- Per-agent customized skill content. The `SKILL.md` is one canonical, agent-agnostic artifact.
- A `print` variant without frontmatter (e.g. for AGENTS.md sections or chat prompts). The canonical artifact includes frontmatter; a stripped variant is YAGNI.
- An `uninstall` subcommand. Users can `rm` the one file; lifecycle management is out of scope.
- Respecting `XDG_CONFIG_HOME` for the opencode dir. The standard `~/.config/opencode` path is used; opencode's backward-compatible scanning of `~/.claude/skills/` and `~/.agents/skills/` covers non-standard XDG layouts.

## Command surface

```
superseded skill install [--agent <name>]... [--force]
superseded skill print
```

A new `skill` subgroup of the top-level `cli` group.

### `superseded skill install`

- `--agent <name>` / `-a` — repeatable; one of `claude-code`, `opencode`, `codex`. Default: all three. Unknown value → exit `2` with the valid list.
- `--force` — required to overwrite a target whose existing content **differs** from the generated skill. No effect when the target is absent or already content-identical.
- `--config` is **not** accepted (unlike `init`/`review`): the install target is the user home, not a repo config.

### `superseded skill print`

- Takes no flags. Writes the canonical `SKILL.md` (frontmatter + body) to **stdout**. Performs **no** filesystem writes.
- This is the "GitHub prompt" delivery path: pipe it, paste it into a chat, or commit it to `.github/`/`.claude/skills/`/`.agents/skills/`.

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | `install`: all selected targets written or content-identical no-ops. `print`: emitted. |
| `2` | Misuse: unknown `--agent`; or ≥1 selected target had *differing* existing content without `--force` (those targets are skipped and named on stderr; the remaining targets still write). |

### Example output (stderr)

```
Installed skill for: claude-code, opencode, codex
Wrote 3 file(s) to ~/.claude/skills, ~/.config/opencode/skills, ~/.agents/skills
```

On a content-identical re-run:
```
Already up to date: claude-code, opencode, codex
```

On conflict without `--force`:
```
Skipped (differs, use --force): opencode
Installed skill for: claude-code, codex
Error: 1 target(s) skipped. Re-run with --force to overwrite.
```

All status text goes to stderr via the existing `_status` helper so stdout stays clean for piping (and so `print` owns stdout).

## The generated skill

A single canonical `SKILL.md`, agent-agnostic, written verbatim to every selected target. Location convention: `<personal skills root>/superseded/SKILL.md`.

````markdown
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
````

### Why this content works

- The frontmatter `description` lists exact user phrases ("review this PR", "superseded", "code review") so agent skill discovery matches the triggering request.
- The opening paragraph authoritatively grounds that `superseded` is installed, preempting the "let me check what's installed" detour.
- The `## Rules` block is an explicit anti-rationalization layer (in the style of discipline-enforcing skills) that forbids the two failure modes observed in the wild: probing for the tool, and substituting a manual review.
- Output shapes (`severity`, fields, exit `3`) are accurate to `models.py` / `cli.py` so the agent can summarize findings correctly.

## File locations & targeting

| Agent | Personal skill dir |
|-------|--------------------|
| claude-code | `~/.claude/skills/superseded/SKILL.md` |
| opencode | `~/.config/opencode/skills/superseded/SKILL.md` |
| codex | `~/.agents/skills/superseded/SKILL.md` |

`~` resolves via `Path.home()`.

Coverage guarantee: opencode's skill discovery also scans `~/.claude/skills/` and `~/.agents/skills/` for backward compatibility, and claude-code/codex each read their own dir. Writing all three (the default) therefore ensures every agent finds the skill in its native home without relying on compat scanning. The mapping is fixed and lives next to the generator so it never drifts from the agent list.

## Architecture

Two new/extended pieces:

1. **`src/superseded/skill.py`** (new) — pure logic, no click, no stdout. Fully unit-testable.
2. **`src/superseded/cli.py`** (extended) — add the `skill` group composing the above.

`review`, `init`, `feedback`, `serve`, `migrate` are unchanged. No new dependencies.

### `skill.py`

```python
from __future__ import annotations

import os
from pathlib import Path

SKILL_NAME = "superseded"
SKILL_AGENTS: tuple[str, ...] = ("claude-code", "opencode", "codex")

# agent name -> (home-relative) personal skills root
_AGENT_SKILLS_ROOT: dict[str, tuple[str, ...]] = {
    "claude-code": (".claude", "skills"),
    "opencode": (".config", "opencode", "skills"),
    "codex": (".agents", "skills"),
}


def build_skill_text() -> str:
    """Return the canonical superseded SKILL.md content (deterministic)."""
    ...


def skill_dir_for(agent_name: str) -> Path:
    """Resolve the personal skills dir for an agent, e.g. ~/.claude/skills/superseded."""
    return Path.home().joinpath(*_AGENT_SKILLS_ROOT[agent_name], SKILL_NAME)


def install_skill(
    agents: list[str],
    *,
    force: bool = False,
    status: Callable[[str], None] | None = None,
) -> tuple[list[str], list[str]]:
    """Write SKILL.md to each selected agent's personal dir.

    Returns (written, skipped_conflict). A target is written when it is absent,
    already content-identical, or present with force=True. Writes are atomic
    (tmp + os.replace). Never raises on conflict — reports via the return value.
    """
    ...
```

Notes:
- The skill body is a module-level constant assembled in `build_skill_text()` (single source of truth shared by `install` and `print`).
- `_AGENT_SKILLS_ROOT` is private; `skill_dir_for` is the public resolver and the only place `Path.home()` is read (so tests monkeypatch `Path.home` once).
- Atomic write reuses the temp-file + `os.replace` pattern from `config.write_config`.
- `install_skill` never raises on conflict; it returns the conflict list so the CLI layer decides the exit code. This keeps the pure function side-effect-free w.r.t. the process.

### `cli.py` — `skill` group

```python
@cli.group()
def skill() -> None:
    """Install or print the superseded agent skill."""


@skill.command("install")
@click.option(
    "--agent", "-a", "agents", multiple=True,
    help="Limit to a specific agent (repeatable). One of: claude-code, opencode, codex.",
)
@click.option("--force", is_flag=True, help="Overwrite a target whose content differs.")
@click.pass_context
def skill_install(ctx, agents: tuple[str, ...], force: bool) -> None:
    """Install the superseded SKILL.md into each agent's personal skill dir."""
    setup_logging(...)
    _run_skill_install(selected=agents, force=force)


@skill.command("print")
@click.pass_context
def skill_print(ctx) -> None:
    """Print the canonical superseded SKILL.md to stdout."""
    setup_logging(...)
    click.echo(build_skill_text())
```

`_run_skill_install` flow:

1. Resolve `selected = list(agents) or list(SKILL_AGENTS)`.
2. Validate each is in `SKILL_AGENTS`; else `_status` error + `sys.exit(2)`.
3. `written, skipped = install_skill(selected, force=force, status=_status)`.
4. Status summary: written names + "Already up to date" for identical no-ops.
5. If `skipped`: `_status` naming them + the `--force` hint, then `sys.exit(2)`.
6. Else `sys.exit(0)` implicitly.

Logging setup mirrors `init` (`resolve_log_format` / `resolve_log_level` from `ctx.obj`).

## Testing

### New: `tests/test_skill.py`

All tests monkeypatch `Path.home()` to a `tmp_path`; no real filesystem mutation, no network.

**Pure-module unit tests:**

- `test_build_skill_text_has_frontmatter` — starts with `---`; contains `name: superseded`, a `description:` line, the "Do not probe" rule, and the `superseded review --pr` invocation.
- `test_skill_dir_for_each_agent` — claude-code ends with `.claude/skills/superseded`; opencode with `.config/opencode/skills/superseded`; codex with `.agents/skills/superseded`.
- `test_install_writes_all_three` — `install_skill(list(SKILL_AGENTS))` writes three `SKILL.md` files, each matching `build_skill_text()`.
- `test_install_specific_agent` — `["claude-code"]` writes only that dir; the other two roots do not exist.
- `test_install_identical_is_noop` — pre-write identical content, no `force` → agent in `written` (treated as success), file bytes unchanged.
- `test_install_refuses_diff_without_force` — pre-write differing content, no `force` → agent in `skipped`, file unchanged.
- `test_install_force_overwrites_diff` — pre-write differing content + `force=True` → in `written`, file matches `build_skill_text()`.
- `test_install_atomic_no_tmp_lingering` — after a write, no `*.tmp` file remains beside the target.

**CLI integration tests** (`click.testing.CliRunner`, monkeypatched `Path.home`):

- `test_cli_skill_install_happy_path` — exit `0`; three files written; status text on stderr.
- `test_cli_skill_print_emits_skill` — stdout equals `build_skill_text()`; no skill files written under tmp home.
- `test_cli_skill_install_unknown_agent` — `--agent bogus` → exit `2` with valid-list message.
- `test_cli_skill_install_force_flag` — differing existing file + `--force` → exit `0`, overwritten; same setup without `--force` → exit `2`.

All tests follow existing patterns: `from __future__ import annotations`, monkeypatching rather than real PATH/home mutation, no network.

## Documentation updates

- `AGENTS.md` "Commands" block — add:
  ```
  uv run superseded skill install                # install SKILL.md into each agent's personal skill dir
  uv run superseded skill print                  # emit the SKILL.md to stdout (paste/commit)
  ```
- `AGENTS.md` "Architecture notes" — one line: ``superseded skill install`` writes a canonical `SKILL.md` (built in `skill.py`) into the personal skill dirs of claude-code/opencode/codex so agents invoke `superseded review` directly.
- `README.md` — one setup line next to the existing `superseded init` mention.
- `index.html` (landing page) — add a feature card in the `#features` grid announcing the agent-skill install, so the capability is surfaced on the marketing page.

## Risk / trade-offs

- **Global, not per-repo.** A personal install applies to every repo the user opens. Accepted: matches "add a skill to claude/opencode/codex". Project-scoped install (`--project` writing `.claude/skills/`, `.opencode/skills/`, `.agents/skills/`) is a natural future extension.
- **Hardcoded paths can drift from agent conventions.** Mitigation: opencode redundantly discovers via the claude/agents compat paths, and the mapping is a single private dict co-located with the generator.
- **Skill content can go stale as the CLI evolves** (new flags, changed severities). Mitigation: content is minimal and focuses on stable surfaces (`review --pr/--diff/--format json`, the four severities, exit `3`); a re-run after upgrade refreshes it (identical → no-op; changed → `--force`).
- **Content-identical no-op differs from `init`** (which refuses any existing file without `--force`). Accepted improvement: makes `skill install` safely re-runnable to self-heal after upgrades.
