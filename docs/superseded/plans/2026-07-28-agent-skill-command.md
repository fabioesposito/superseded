# Agent Skill Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `superseded skill` command group (`install` + `print`) that writes/prints a canonical `SKILL.md` so claude-code, opencode, and codex invoke `superseded review` directly.

**Architecture:** One new pure module `src/superseded/skill.py` (content generation + idempotent atomic installer, no click/IO). A new `skill` subgroup in `src/superseded/cli.py` composes it. No new deps. Tests monkeypatch `Path.home()`.

**Tech Stack:** Python 3.14+, click, pytest, ruff. Run everything via `uv run`.

**Spec:** `docs/superseded/specs/2026-07-28-agent-skill-command-design.md`

---

## File Structure

- **Create** `src/superseded/skill.py` — `build_skill_text()`, `skill_dir_for(agent)`, `install_skill(agents, *, force, status)`, plus `SKILL_NAME` / `SKILL_AGENTS` constants. No click, no stdout.
- **Create** `tests/test_skill.py` — pure-module + CliRunner tests; all monkeypatch `Path.home()`.
- **Modify** `src/superseded/cli.py` — add `skill` group with `install` + `print` subcommands and `_run_skill_install`.
- **Modify** `AGENTS.md` — commands block + architecture note.
- **Modify** `README.md` — one setup line.
- `index.html` — already updated (feature card) during brainstorming.

---

### Task 1: `skill.py` — canonical skill text + resolver

**Files:**
- Create: `src/superseded/skill.py`
- Test: `tests/test_skill.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_skill.py`:

```python
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from superseded.cli import cli
from superseded.skill import SKILL_AGENTS, build_skill_text, install_skill, skill_dir_for


def test_build_skill_text_has_frontmatter():
    text = build_skill_text()
    assert text.startswith("---\n")
    assert "name: superseded" in text
    assert "description:" in text
    # anti-rationalization + invocation must be present
    assert "Do not probe PATH" in text or "Do not verify superseded is installed" in text
    assert "superseded review --pr" in text


def test_skill_dir_for_each_agent():
    for name in SKILL_AGENTS:
        d = skill_dir_for(name)
        assert d.name == "superseded"
        assert d.parent.name == "skills"
    assert skill_dir_for("claude-code").parts[-3] == ".claude"
    assert skill_dir_for("opencode").parts[-3] == "opencode"
    assert skill_dir_for("codex").parts[-3] == ".agents"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_skill.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'superseded.skill'`

- [ ] **Step 3: Write minimal implementation**

Create `src/superseded/skill.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_skill.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Lint**

Run: `uv run ruff check src/superseded/skill.py tests/test_skill.py && uv run ruff format src/superseded/skill.py tests/test_skill.py`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add src/superseded/skill.py tests/test_skill.py
git commit -m "feat(skill): add canonical SKILL.md generator and agent dir resolver"
```

---

### Task 2: `skill.py` — idempotent installer

**Files:**
- Modify: `src/superseded/skill.py` (add `install_skill`)
- Test: `tests/test_skill.py` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_skill.py`:

```python
def _patch_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))


def test_install_writes_all_three(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    written, skipped = install_skill(list(SKILL_AGENTS))
    assert skipped == []
    assert set(written) == set(SKILL_AGENTS)
    for name in SKILL_AGENTS:
        f = skill_dir_for(name) / "SKILL.md"
        assert f.exists()
        assert f.read_text() == build_skill_text()


def test_install_specific_agent(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    written, _ = install_skill(["claude-code"])
    assert written == ["claude-code"]
    assert (skill_dir_for("claude-code") / "SKILL.md").exists()
    assert not skill_dir_for("opencode").exists()
    assert not skill_dir_for("codex").exists()


def test_install_identical_is_noop(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    install_skill(["codex"])  # seed
    f = skill_dir_for("codex") / "SKILL.md"
    mtime_before = f.stat().st_mtime_ns
    written, skipped = install_skill(["codex"])  # re-run, identical, no force
    assert skipped == []
    assert written == ["codex"]
    assert f.read_text() == build_skill_text()
    assert f.stat().st_mtime_ns == mtime_before  # not rewritten


def test_install_refuses_diff_without_force(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    f = skill_dir_for("codex") / "SKILL.md"
    f.parent.mkdir(parents=True)
    f.write_text("different content")
    written, skipped = install_skill(["codex"])
    assert written == []
    assert skipped == ["codex"]
    assert f.read_text() == "different content"  # unchanged


def test_install_force_overwrites_diff(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    f = skill_dir_for("codex") / "SKILL.md"
    f.parent.mkdir(parents=True)
    f.write_text("different content")
    written, skipped = install_skill(["codex"], force=True)
    assert skipped == []
    assert written == ["codex"]
    assert f.read_text() == build_skill_text()


def test_install_atomic_no_tmp_lingering(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    install_skill(["opencode"])
    d = skill_dir_for("opencode")
    assert list(d.glob("*.tmp")) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_skill.py -v`
Expected: FAIL — `ImportError: cannot import name 'install_skill'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/superseded/skill.py` (after `skill_dir_for`):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_skill.py -v`
Expected: PASS (all 8 tests)

- [ ] **Step 5: Lint**

Run: `uv run ruff check src/superseded/skill.py tests/test_skill.py && uv run ruff format src/superseded/skill.py tests/test_skill.py`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add src/superseded/skill.py tests/test_skill.py
git commit -m "feat(skill): add idempotent atomic installer"
```

---

### Task 3: `cli.py` — wire the `skill` group

**Files:**
- Modify: `src/superseded/cli.py` (add import + `skill` group + `_run_skill_install`)
- Test: `tests/test_skill.py` (append CLI tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_skill.py`:

```python
def test_cli_skill_install_happy_path(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["skill", "install"])
    assert result.exit_code == 0, result.output
    for name in SKILL_AGENTS:
        assert (skill_dir_for(name) / "SKILL.md").read_text() == build_skill_text()


def test_cli_skill_print_emits_skill(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["skill", "print"])
    assert result.exit_code == 0, result.output
    assert result.output == build_skill_text() + "\n"
    # print performs no writes
    for name in SKILL_AGENTS:
        assert not skill_dir_for(name).exists()


def test_cli_skill_install_unknown_agent(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["skill", "install", "--agent", "bogus"])
    assert result.exit_code == 2
    assert "bogus" in result.output


def test_cli_skill_install_force_flag(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    f = skill_dir_for("codex") / "SKILL.md"
    f.parent.mkdir(parents=True)
    f.write_text("old")
    runner = CliRunner()
    no_force = runner.invoke(cli, ["skill", "install", "--agent", "codex"])
    assert no_force.exit_code == 2
    assert f.read_text() == "old"
    forced = runner.invoke(cli, ["skill", "install", "--agent", "codex", "--force"])
    assert forced.exit_code == 0, forced.output
    assert f.read_text() == build_skill_text()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_skill.py -v`
Expected: FAIL — `UsageError: No such command 'skill'`

- [ ] **Step 3: Write minimal implementation**

In `src/superseded/cli.py`, add to the existing import block near the top (after the `from superseded.review.executor import ...` line):

```python
from superseded.skill import SKILL_AGENTS, build_skill_text, install_skill
```

Add the new command group anywhere among the other top-level commands (e.g. after the `init` command / `_run_init`):

```python
@cli.group()
@click.pass_context
def skill(ctx: click.Context) -> None:
    """Install or print the superseded agent skill."""


@skill.command("install")
@click.option(
    "--agent",
    "-a",
    "agents",
    multiple=True,
    help="Limit to a specific agent (repeatable). One of: claude-code, opencode, codex.",
)
@click.option("--force", is_flag=True, help="Overwrite a target whose content differs.")
@click.pass_context
def skill_install(ctx: click.Context, agents: tuple[str, ...], force: bool) -> None:
    """Install the superseded SKILL.md into each agent's personal skill dir."""
    setup_logging(
        resolve_log_format(ctx.obj.get("log_format") if ctx.obj else None),
        resolve_log_level(ctx.obj.get("log_level") if ctx.obj else None),
    )
    _run_skill_install(selected=list(agents), force=force)


@skill.command("print")
@click.pass_context
def skill_print(ctx: click.Context) -> None:
    """Print the canonical superseded SKILL.md to stdout."""
    setup_logging(
        resolve_log_format(ctx.obj.get("log_format") if ctx.obj else None),
        resolve_log_level(ctx.obj.get("log_level") if ctx.obj else None),
    )
    click.echo(build_skill_text())


def _run_skill_install(selected: list[str], force: bool) -> None:
    targets = selected or list(SKILL_AGENTS)
    unknown = [a for a in targets if a not in SKILL_AGENTS]
    if unknown:
        click.echo(
            f"Error: unknown agent(s): {', '.join(unknown)}. "
            f"Choose from: {', '.join(SKILL_AGENTS)}",
            err=True,
        )
        sys.exit(2)

    written, skipped = install_skill(targets, force=force, status=_status)
    if written:
        _status(f"Installed skill for: {', '.join(written)}")
    if skipped:
        _status(f"Skipped (differs, use --force): {', '.join(skipped)}")
        sys.exit(2)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_skill.py -v`
Expected: PASS (all 12 tests)

- [ ] **Step 5: Full test suite (no regressions)**

Run: `uv run pytest tests/ -q`
Expected: PASS (all pre-existing tests still green)

- [ ] **Step 6: Lint + format**

Run: `uv run ruff check src/ tests/ && uv run ruff format src/ tests/`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add src/superseded/cli.py tests/test_skill.py
git commit -m "feat(cli): add superseded skill install/print command group"
```

---

### Task 4: Docs + landing page + final verify

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Verify: `index.html` (already edited in brainstorming)

- [ ] **Step 1: Smoke-test the binary end to end**

Run:
```bash
uv run superseded skill print | head -5
uv run superseded --help | grep skill
```
Expected: `print` emits the frontmatter; `--help` lists the `skill` group.

- [ ] **Step 2: Update AGENTS.md commands block**

In the "Commands (run from repo root)" bash block, add after the `superseded init` line:

```
uv run superseded skill install                # install SKILL.md into each agent's personal skill dir
uv run superseded skill print                  # emit the SKILL.md to stdout (paste/commit)
```

In the "Architecture notes" section, add a bullet:

```
- `superseded skill install` writes a canonical `SKILL.md` (built in `skill.py`) into the personal skill dirs of claude-code (`~/.claude/skills`), opencode (`~/.config/opencode/skills`), and codex (`~/.agents/skills`) so agents invoke `superseded review` directly instead of probing PATH. `superseded skill print` emits the same file to stdout.
```

- [ ] **Step 3: Update README.md**

Add one setup line next to the existing `superseded init` mention:

```bash
superseded skill install   # one-time: teach claude-code/opencode/codex to run superseded review directly
```

- [ ] **Step 4: Final verification**

Run: `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest tests/ -q`
Expected: lint clean, format clean, all tests pass.

- [ ] **Step 5: Commit + push**

```bash
git add AGENTS.md README.md index.html docs/superseded/plans/2026-07-28-agent-skill-command.md
git commit -m "docs: document superseded skill command; add landing-page card"
git push origin main
```

---

## Self-Review (completed during planning)

- **Spec coverage:** build_skill_text (T1), skill_dir_for (T1), install_skill + idempotency/atomicity (T2), CLI install+print+exit codes (T3), docs+landing+verify (T4). All spec sections mapped.
- **Placeholder scan:** every code step contains real code; no TBD/TODO.
- **Type consistency:** `install_skill(agents, *, force, status) -> (written, skipped)` matches across T2/T3; `SKILL_AGENTS` tuple used consistently; `_run_skill_install` consumes the same return shape.
