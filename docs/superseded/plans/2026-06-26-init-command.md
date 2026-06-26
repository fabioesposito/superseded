# `superseded init` Command — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a non-interactive `superseded init` command that detects installed AI review CLIs (and `gh`), picks a default agent + model, and writes a `.superseded.yaml` config file.

**Architecture:** A new pure-logic `detection.py` module wraps the existing `AGENT_MAP` and `Agent.is_available()`. A small `write_config` helper is added to `config.py`. A thin click `init` command in `cli.py` composes the two. Detection and config-writing are unit-tested in isolation; the CLI command is integration-tested via `click.testing.CliRunner` with monkeypatched detection functions.

**Tech Stack:** Python 3.14+, click 8, pydantic v2, PyYAML, pytest with `pytest-asyncio` (auto mode). Lint/format via `ruff`. All commands run through `uv run`.

**Reference spec:** `docs/superseded/specs/2026-06-26-init-command-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/superseded/detection.py` | Create | Pure detection logic: `detect_agents`, `detect_gh`, `pick_agent`, `default_model_for`, `AgentStatus`, `AGENT_PREFERENCE`, `DEFAULT_MODELS`. No click, no filesystem writes. |
| `src/superseded/config.py` | Modify | Add `write_config(config, path)` doing an atomic YAML write. Add `import os`. |
| `src/superseded/cli.py` | Modify | Add `init` command + `_run_init` helper. |
| `tests/test_detection.py` | Create | Unit tests for `detection.py` (monkeypatch `shutil.which`). |
| `tests/test_config.py` | Modify | Add `write_config` round-trip / atomic / nested-passes tests. |
| `tests/test_init.py` | Create | CLI integration tests via `CliRunner`. |
| `AGENTS.md` | Modify | Mention `init` command and `detection.py` in Architecture notes + Commands block. |

No other modules change. `review.engine.AGENT_MAP` is imported by `detection.py` but not modified.

---

## Task 1: Create `detection.py` with `AgentStatus`, `AGENT_PREFERENCE`, `DEFAULT_MODELS`

**Files:**
- Create: `src/superseded/detection.py`
- Test: `tests/test_detection.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_detection.py`:

```python
from __future__ import annotations

from superseded.detection import (
    AGENT_PREFERENCE,
    DEFAULT_MODELS,
    AgentStatus,
)


def test_agent_preference_order():
    assert AGENT_PREFERENCE == ("claude-code", "opencode", "codex")


def test_default_models_contains_known_agents():
    assert DEFAULT_MODELS["claude-code"] == "claude-sonnet-4-6"
    assert DEFAULT_MODELS["codex"] == "gpt-5.4-mini"
    assert "opencode" not in DEFAULT_MODELS


def test_agent_status_is_frozen_dataclass():
    s = AgentStatus(name="opencode", available=True, binary="opencode")
    assert s.name == "opencode"
    assert s.available is True
    assert s.binary == "opencode"
    try:
        s.name = "x"  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("AgentStatus must be frozen")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_detection.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'superseded.detection'`.

- [ ] **Step 3: Create `src/superseded/detection.py`**

```python
from __future__ import annotations

from dataclasses import dataclass


AGENT_PREFERENCE: tuple[str, ...] = ("claude-code", "opencode", "codex")

# Per the design: hardcoded default model per agent.
# opencode is intentionally omitted so default_model_for returns None,
# letting the opencode CLI pick its own default model at runtime.
DEFAULT_MODELS: dict[str, str] = {
    "claude-code": "claude-sonnet-4-6",
    "codex": "gpt-5.4-mini",
}


@dataclass(frozen=True)
class AgentStatus:
    name: str
    available: bool
    binary: str
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_detection.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check src/superseded/detection.py tests/test_detection.py && uv run ruff format src/superseded/detection.py tests/test_detection.py`

- [ ] **Step 6: Commit**

```bash
git add src/superseded/detection.py tests/test_detection.py
git commit -m "feat(init): add detection module constants and AgentStatus"
```

---

## Task 2: Add `detect_agents`, `detect_gh`, `pick_agent`, `default_model_for` to `detection.py`

**Files:**
- Modify: `src/superseded/detection.py`
- Test: `tests/test_detection.py`

- [ ] **Step 1: Append failing tests to `tests/test_detection.py`**

Add at the end of the file:

```python
from superseded.detection import (
    default_model_for,
    detect_agents,
    detect_gh,
    pick_agent,
)


def test_detect_agents_returns_all_three(monkeypatch):
    # Make every binary resolve to a fake path.
    monkeypatch.setattr(
        "superseded.detection.shutil.which", lambda b: f"/usr/bin/{b}"
    )
    statuses = detect_agents()
    names = {s.name for s in statuses}
    assert names == {"claude-code", "opencode", "codex"}
    for s in statuses:
        assert s.available is True
        assert s.binary


def test_detect_agents_marks_missing_unavailable(monkeypatch):
    def fake_which(b: str) -> str | None:
        return None if b == "codex" else f"/usr/bin/{b}"

    monkeypatch.setattr("superseded.detection.shutil.which", fake_which)
    statuses = {s.name: s for s in detect_agents()}
    assert statuses["codex"].available is False
    assert statuses["opencode"].available is True


def test_detect_gh_true(monkeypatch):
    monkeypatch.setattr("superseded.detection.shutil.which", lambda b: "/usr/bin/gh")
    assert detect_gh() is True


def test_detect_gh_false(monkeypatch):
    monkeypatch.setattr("superseded.detection.shutil.which", lambda b: None)
    assert detect_gh() is False


def test_pick_agent_returns_highest_preference():
    assert pick_agent(["opencode", "codex"]) == "opencode"
    assert pick_agent(["claude-code", "codex"]) == "claude-code"
    assert pick_agent(["codex"]) == "codex"


def test_pick_agent_none_when_empty():
    assert pick_agent([]) is None


def test_default_model_for_known_agents():
    assert default_model_for("claude-code") == "claude-sonnet-4-6"
    assert default_model_for("codex") == "gpt-5.4-mini"


def test_default_model_for_opencode_is_none():
    assert default_model_for("opencode") is None


def test_default_model_for_unknown_is_none():
    assert default_model_for("bogus") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_detection.py -v`
Expected: FAIL — `ImportError: cannot import name 'detect_agents' ...` for the new imports.

- [ ] **Step 3: Extend `src/superseded/detection.py`**

Replace the entire file contents with:

```python
from __future__ import annotations

import shutil
from dataclasses import dataclass

from superseded.review.engine import AGENT_MAP


AGENT_PREFERENCE: tuple[str, ...] = ("claude-code", "opencode", "codex")

# Per the design: hardcoded default model per agent.
# opencode is intentionally omitted so default_model_for returns None,
# letting the opencode CLI pick its own default model at runtime.
DEFAULT_MODELS: dict[str, str] = {
    "claude-code": "claude-sonnet-4-6",
    "codex": "gpt-5.4-mini",
}


@dataclass(frozen=True)
class AgentStatus:
    name: str
    available: bool
    binary: str


def detect_agents() -> list[AgentStatus]:
    """Probe all registered agents; return one AgentStatus per agent (any order)."""
    statuses: list[AgentStatus] = []
    for name, cls in AGENT_MAP.items():
        agent = cls(model=None)
        binary = agent.build_command()[0]
        statuses.append(
            AgentStatus(name=name, available=agent.is_available(), binary=binary)
        )
    return statuses


def detect_gh() -> bool:
    return shutil.which("gh") is not None


def pick_agent(available: list[str]) -> str | None:
    """Return the highest-preference agent name present in `available`, else None."""
    for name in AGENT_PREFERENCE:
        if name in available:
            return name
    return None


def default_model_for(agent: str) -> str | None:
    return DEFAULT_MODELS.get(agent)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_detection.py -v`
Expected: PASS (all tests, including Task 1's).

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check src/superseded/detection.py tests/test_detection.py && uv run ruff format src/superseded/detection.py tests/test_detection.py`

- [ ] **Step 6: Commit**

```bash
git add src/superseded/detection.py tests/test_detection.py
git commit -m "feat(init): add detect_agents/detect_gh/pick_agent/default_model_for"
```

---

## Task 3: Add `write_config` to `config.py`

**Files:**
- Modify: `src/superseded/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Append failing tests to `tests/test_config.py`**

Add at the end of the file:

```python
def test_write_config_round_trip(tmp_path):
    from superseded.config import Config, load_config, write_config

    cfg = Config(agent="claude-code", model="claude-sonnet-4-6")
    target = tmp_path / ".superseded.yaml"
    write_config(cfg, target)

    loaded = load_config(target)
    assert loaded.agent == "claude-code"
    assert loaded.model == "claude-sonnet-4-6"
    assert loaded.passes.security is True
    assert loaded.passes.architecture is True
    assert loaded.format == "table"
    assert loaded.memory is True


def test_write_config_no_temp_lingering(tmp_path):
    from superseded.config import write_config

    target = tmp_path / ".superseded.yaml"
    write_config(Config(), target)
    temps = list(tmp_path.glob("*.tmp"))
    assert temps == []


def test_write_config_contains_nested_passes_block(tmp_path):
    from superseded.config import write_config

    target = tmp_path / ".superseded.yaml"
    write_config(Config(), target)
    text = target.read_text()
    assert "\npasses:" in text or text.startswith("passes:")
    assert "security:" in text


def test_write_config_default_path(tmp_path, monkeypatch):
    from superseded.config import Config, load_config, write_config

    monkeypatch.chdir(tmp_path)
    write_config(Config(agent="codex"))
    # Default path is .superseded.yaml in cwd
    assert (tmp_path / ".superseded.yaml").exists()
    loaded = load_config(None)
    assert loaded.agent == "codex"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'write_config'`.

- [ ] **Step 3: Modify `src/superseded/config.py`**

Replace the entire file contents with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (all config tests including new ones).

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check src/superseded/config.py tests/test_config.py && uv run ruff format src/superseded/config.py tests/test_config.py`

- [ ] **Step 6: Commit**

```bash
git add src/superseded/config.py tests/test_config.py
git commit -m "feat(init): add atomic write_config helper"
```

---

## Task 4: Add the `init` command to `cli.py`

**Files:**
- Modify: `src/superseded/cli.py`
- Test: `tests/test_init.py`

- [ ] **Step 1: Create failing CLI integration tests**

Create `tests/test_init.py`:

```python
from __future__ import annotations

import yaml
from click.testing import CliRunner

from superseded.cli import cli
from superseded.config import load_config
from superseded.detection import AgentStatus


def _patch_detection(
    monkeypatch,
    *,
    agents: list[AgentStatus],
    gh: bool,
) -> None:
    monkeypatch.setattr("superseded.cli.detect_agents", lambda: agents)
    monkeypatch.setattr("superseded.cli.detect_gh", lambda: gh)


def test_init_happy_path(tmp_path, monkeypatch):
    _patch_detection(
        monkeypatch,
        agents=[
            AgentStatus("claude-code", True, "claude"),
            AgentStatus("opencode", True, "opencode"),
            AgentStatus("codex", False, "codex"),
        ],
        gh=True,
    )
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 0, result.output
    assert target.exists()
    cfg = load_config(target)
    assert cfg.agent == "claude-code"
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.passes.security is True
    assert cfg.passes.architecture is True


def test_init_refuses_overwrite_without_force(tmp_path, monkeypatch):
    target = tmp_path / ".superseded.yaml"
    target.write_text("agent: codex\n")
    _patch_detection(
        monkeypatch,
        agents=[AgentStatus("opencode", True, "opencode")],
        gh=True,
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 2
    # Original contents preserved.
    assert yaml.safe_load(target.read_text()) == {"agent": "codex"}


def test_init_force_overwrites(tmp_path, monkeypatch):
    target = tmp_path / ".superseded.yaml"
    target.write_text("agent: codex\n")
    _patch_detection(
        monkeypatch,
        agents=[AgentStatus("claude-code", True, "claude")],
        gh=True,
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--force", "--config", str(target)])
    assert result.exit_code == 0
    cfg = load_config(target)
    assert cfg.agent == "claude-code"


def test_init_no_agents_exit_1(tmp_path, monkeypatch):
    _patch_detection(
        monkeypatch,
        agents=[
            AgentStatus("claude-code", False, "claude"),
            AgentStatus("opencode", False, "opencode"),
            AgentStatus("codex", False, "codex"),
        ],
        gh=True,
    )
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 1
    assert not target.exists()


def test_init_agent_override_unknown(tmp_path, monkeypatch):
    _patch_detection(
        monkeypatch,
        agents=[AgentStatus("opencode", True, "opencode")],
        gh=True,
    )
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--agent", "bogus", "--config", str(target)])
    assert result.exit_code == 2
    stderr = result.stderr_bytes.decode() if result.stderr_bytes else result.output
    assert "bogus" in stderr or "bogus" in result.output


def test_init_agent_override_not_installed(tmp_path, monkeypatch):
    _patch_detection(
        monkeypatch,
        agents=[
            AgentStatus("claude-code", True, "claude"),
            AgentStatus("codex", False, "codex"),
        ],
        gh=True,
    )
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--agent", "codex", "--config", str(target)])
    assert result.exit_code == 2
    assert not target.exists()


def test_init_agent_override_installed(tmp_path, monkeypatch):
    _patch_detection(
        monkeypatch,
        agents=[
            AgentStatus("claude-code", True, "claude"),
            AgentStatus("codex", True, "codex"),
        ],
        gh=True,
    )
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--agent", "codex", "--config", str(target)])
    assert result.exit_code == 0
    cfg = load_config(target)
    assert cfg.agent == "codex"
    assert cfg.model == "gpt-5.4-mini"


def test_init_gh_missing_still_succeeds(tmp_path, monkeypatch):
    _patch_detection(
        monkeypatch,
        agents=[AgentStatus("opencode", True, "opencode")],
        gh=False,
    )
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 0
    assert target.exists()
    cfg = load_config(target)
    assert cfg.agent == "opencode"
    assert cfg.model is None  # opencode has no hardcoded default


def test_init_default_target_when_no_config_flag(tmp_path, monkeypatch):
    _patch_detection(
        monkeypatch,
        agents=[AgentStatus("opencode", True, "opencode")],
        gh=True,
    )
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 0
    assert (tmp_path / ".superseded.yaml").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_init.py -v`
Expected: FAIL — `Error: No such command 'init'` from click.

- [ ] **Step 3: Modify `src/superseded/cli.py`**

3a. Replace the existing import line:

```python
from superseded.config import Config, load_config
```

with:

```python
from superseded.config import Config, load_config, write_config
from superseded.detection import (
    AgentStatus,
    default_model_for,
    detect_agents,
    detect_gh,
    pick_agent,
)
```

(Keep all other imports unchanged.)

3b. Add the `init` command. Insert this block immediately **after** the `_link_comment_ids` async helper and **before** the `@cli.command()` decorator for `feedback`:

```python
@cli.command()
@click.option("--force", is_flag=True, help="Overwrite an existing .superseded.yaml")
@click.option(
    "--agent",
    "agent_override",
    default=None,
    help="Force a specific agent (claude-code, opencode, codex)",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Path to write (default: .superseded.yaml in cwd)",
)
def init(force: bool, agent_override: str | None, config_path: Path | None) -> None:
    """Detect installed AI CLIs and write a .superseded.yaml config file."""
    _run_init(force=force, agent_override=agent_override, config_path=config_path)


def _run_init(force: bool, agent_override: str | None, config_path: Path | None) -> None:
    from superseded.review.engine import AGENT_MAP

    target = config_path or Path(".superseded.yaml")

    if target.exists() and not force:
        _status(f"Error: {target} already exists. Use --force to overwrite.")
        sys.exit(2)

    statuses = detect_agents()
    available = [s.name for s in statuses if s.available]
    missing = [s.name for s in statuses if not s.available]
    if available:
        _status(f"Detected agent CLIs: {', '.join(available)}")
    if missing:
        _status(f"Missing: {', '.join(missing)}")

    gh_ok = detect_gh()
    if gh_ok:
        _status("gh CLI: found")
    else:
        _status("gh CLI: not found (PR features will be disabled)")

    if agent_override is not None:
        if agent_override not in AGENT_MAP:
            _status(
                f"Error: unknown agent '{agent_override}'. "
                f"Choose from: {', '.join(AGENT_MAP)}"
            )
            sys.exit(2)
        if agent_override not in available:
            _status(
                f"Error: selected agent '{agent_override}' is not installed on PATH."
            )
            sys.exit(2)
        chosen = agent_override
    else:
        chosen = pick_agent(available)
        if chosen is None:
            _status(
                "Error: no supported AI CLI found on PATH. "
                "Install one of: claude, opencode, codex."
            )
            sys.exit(1)

    model = default_model_for(chosen)
    cfg = Config(agent=chosen, model=model)
    write_config(cfg, target)

    _status(f"Selected agent: {chosen}" + (f" ({model})" if model else ""))
    _status(f"Wrote {target}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_init.py -v`
Expected: PASS (all 9 tests).

- [ ] **Step 5: Run the full suite to catch regressions**

Run: `uv run pytest tests/ -v`
Expected: PASS (no regressions in `test_cli.py`, `test_config.py`, etc.).

- [ ] **Step 6: Lint and format**

Run: `uv run ruff check src/ tests/ && uv run ruff format src/ tests/`

- [ ] **Step 7: Commit**

```bash
git add src/superseded/cli.py tests/test_init.py
git commit -m "feat(init): add superseded init command"
```

---

## Task 5: Update `AGENTS.md` docs

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Read the current `AGENTS.md`** to confirm exact anchor text.

- [ ] **Step 2: Add `init` to the Commands block**

In the "## Commands" bash block, add this line immediately after the existing `uv run superseded review ...` line:

```bash
uv run superseded init                          # detect AI CLIs + write .superseded.yaml
```

- [ ] **Step 3: Mention `init` and `detection.py` in Architecture notes**

In the "## Architecture notes" section, add this bullet immediately after the existing bullet that ends with "register in `AGENT_MAP` in `review/engine.py`.":

> - `superseded init` is a non-interactive setup command: it probes PATH for the supported AI CLIs (via `src/superseded/detection.py`, which wraps `AGENT_MAP` + `Agent.is_available()`) plus `gh`, picks a default agent + model, and writes a `.superseded.yaml` via `config.write_config`. Refuses to overwrite without `--force`.

- [ ] **Step 4: Verify the file parses sensibly (visual check)**

Re-read the modified sections of `AGENTS.md`.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md
git commit -m "docs(agents): document superseded init and detection module"
```

---

## Task 6: Final verification

**Files:** none.

- [ ] **Step 1: Run the entire test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 2: Run ruff across the repo**

Run: `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: No errors.

- [ ] **Step 3: Smoke-test the command manually** (optional, environment-dependent)

From the repo root (with at least one AI CLI on PATH):
```bash
uv run superseded init --config /tmp/.superseded-smoke.yaml
cat /tmp/.superseded-smoke.yaml
rm /tmp/.superseded-smoke.yaml
```
Expected: status lines on stderr, a YAML file written to `/tmp/`.

- [ ] **Step 4: Confirm no DB or gitignored artifacts were committed**

Run: `git status`
Expected: clean working tree, no `.superseded/memory.db` or `*.db` staged.

---

## Self-Review

**Spec coverage check:**
- Command surface (`init`, `--force`, `--agent`, `--config`) -> Task 4.
- Exit codes (`0`/`1`/`2`) -> Task 4 tests (`test_init_happy_path`, `test_init_no_agents_exit_1`, `test_init_refuses_overwrite_without_force`, `test_init_agent_override_unknown`, `test_init_agent_override_not_installed`).
- Example stderr output -> Task 4 `_run_init` `_status` calls.
- `detection.py` (all 4 functions + `AgentStatus` + constants) -> Tasks 1 & 2.
- `config.write_config` (atomic, nested passes, default path) -> Task 3.
- `cli.py` `init` command flow (10 steps) -> Task 4 `_run_init`.
- Testing - detection unit tests -> Task 2. Config tests -> Task 3. CLI integration tests -> Task 4.
- Documentation updates -> Task 5.

No gaps.

**Placeholder scan:** No TBD/TODO. Every code step shows complete code. No "similar to Task N" references.

**Type/name consistency:**
- `AgentStatus(name, available, binary)` - used identically in Task 1, Task 2, Task 4 tests.
- `detect_agents() -> list[AgentStatus]` - consistent across Tasks 1, 2, 4.
- `pick_agent(available: list[str]) -> str | None` - consistent.
- `default_model_for(agent) -> str | None` - consistent.
- `write_config(config, path)` - consistent in Task 3 (definition) and Task 4 (call).
- `agent_override` - used as the click option `dest` and the `_run_init` parameter name consistently.
- All imports added in Task 4 Step 3a (`Config, load_config, write_config` from `config`; the 5 names from `detection`) match what `_run_init` references.
