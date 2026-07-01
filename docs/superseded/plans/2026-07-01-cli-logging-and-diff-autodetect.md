# CLI Structured Logging & Git Diff Auto-Detect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--log-format json` / `--log-level` to the CLI (unifying with server logging) and make `superseded review` auto-detect `git diff HEAD` (or `git diff --cached` with `--staged`) when no `--pr`/`--diff`/FILES is supplied.

**Architecture:** Extract `JsonFormatter` into a shared `superseded/logging_utils.py` with an idempotent `setup_logging(fmt, level)`; the server re-imports it for back-comat. The CLI gains group-level `--log-format`/`--log-level` options whose resolved value (env > flag > config) feeds a per-command `setup_logging()` call. `diff.fetch_diff` gains a `staged` flag and auto-detects the working-tree/index diff when nothing else is specified, raising a friendly `ValueError` on an empty diff.

**Tech Stack:** Python 3.14+, click, pydantic v2, pytest + pytest-asyncio (asyncio_mode = "auto"). Lint/format via ruff. Run everything via `uv run`.

**Spec:** `docs/superseded/specs/2026-07-01-cli-logging-and-diff-autodetect-design.md`

**Conventions (from AGENTS.md):**
- Every module starts with `from __future__ import annotations`.
- Ruff rule set `E,W,F,I,N,UP,B,SIM,TCH,RUF` (ignores `E501,B008,TC001-003,E741`), line length 100, double quotes, isort `known-first-party = ["superseded"]`.
- Run everything via `uv run` (system python may be 3.13): `uv run pytest`, `uv run ruff check`, `uv run ruff format`.
- No comments in code unless explicitly part of a requested docstring.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/superseded/logging_utils.py` | `JsonFormatter` (moved) + `setup_logging(fmt, level)`. | **Create** |
| `src/superseded/server/lifecycle.py` | Re-export `JsonFormatter` from `logging_utils` for back-comat. | Modify |
| `src/superseded/config.py` | Add `log_format`/`log_level` defaults to `Config`. | Modify |
| `src/superseded/cli.py` | Group-level `--log-format`/`--log-level`; `resolve_log_*` helpers; per-command `setup_logging()`; `--staged` flag; drop no-args hard error; thread `staged` into `_run_review`/`fetch_diff`. | Modify |
| `src/superseded/diff.py` | `fetch_diff(staged=...)`; `_fetch_raw_diff` helper; auto-detect + empty-diff error. | Modify |
| `tests/test_logging.py` | Cover `setup_logging` (text/json), idempotency, `JsonFormatter` extras/exc_info. | **Create** |
| `tests/test_config.py` | Cover new `log_format`/`log_level` defaults. | Modify |
| `tests/test_diff.py` | Cover `staged`/HEAD auto-detect + empty-diff error. | Modify |
| `tests/test_cli.py` | Cover `--log-format` wiring, `--staged` wiring, no-args no longer errors. | Modify |

---

## Task 1: Create `logging_utils.py` with `JsonFormatter` + `setup_logging`

**Files:**
- Create: `src/superseded/logging_utils.py`
- Test: `tests/test_logging.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_logging.py`:

```python
from __future__ import annotations

import json
import logging

from superseded.logging_utils import JsonFormatter, setup_logging


def test_setup_logging_text_writes_to_stderr(capsys):
    logging.getLogger().handlers.clear()
    setup_logging("text", "INFO")
    logging.getLogger("superseded.test").info("hello")
    captured = capsys.readouterr()
    assert "hello" in captured.err
    assert "INFO" in captured.err


def test_setup_logging_json_emits_json_line(capsys):
    logging.getLogger().handlers.clear()
    setup_logging("json", "INFO")
    logging.getLogger("superseded.test").info("structured")
    line = capsys.readouterr().err.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "structured"
    assert payload["level"] == "INFO"
    assert "time" in payload


def test_setup_logging_is_idempotent():
    logging.getLogger().handlers.clear()
    setup_logging("text", "INFO")
    first = len(logging.getLogger().handlers)
    setup_logging("json", "WARNING")
    second = len(logging.getLogger().handlers)
    assert first == 1
    assert second == 1


def test_setup_logging_default_level_silences_info(capsys):
    logging.getLogger().handlers.clear()
    setup_logging("text")
    logging.getLogger("superseded.test").info("quiet")
    logging.getLogger("superseded.test").warning("loud")
    err = capsys.readouterr().err
    assert "quiet" not in err
    assert "loud" in err


def test_json_formatter_includes_extra_fields():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="x", level=logging.INFO, pathname=__file__, lineno=1,
        msg="msg", args=(), exc_info=None,
    )
    record.request_id = "abc"
    out = formatter.format(record)
    payload = json.loads(out)
    assert payload["event"] == "msg"
    assert payload["request_id"] == "abc"


def test_json_formatter_serializes_exc_info():
    formatter = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        record = logging.LogRecord(
            name="x", level=logging.ERROR, pathname=__file__, lineno=1,
            msg="failed", args=(), exc_info=sys.exc_info(),
        )
    payload = json.loads(formatter.format(record))
    assert "ValueError" in payload["exc_info"]
    assert "boom" in payload["exc_info"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_logging.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'superseded.logging_utils'`.

- [ ] **Step 3: Create the module**

Create `src/superseded/logging_utils.py`:

```python
from __future__ import annotations

import json
import logging
import sys

_RESERVED_LOG_FIELDS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "getMessage",
    }
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "event": record.getMessage(),
            "level": record.levelname,
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_FIELDS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(fmt: str = "text", level: str = "WARNING") -> None:
    """Configure the root logger with a single stderr handler.

    Idempotent: clears existing handlers first so repeated calls never
    accumulate. ``fmt`` selects a human ("text") or JSON formatter; ``level``
    gates which records surface (applied to the root logger so third-party
    libraries respect it too).
    """
    numeric = getattr(logging, level.upper(), logging.WARNING)
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(numeric)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_logging.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check src/superseded/logging_utils.py tests/test_logging.py && uv run ruff format src/superseded/logging_utils.py tests/test_logging.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/superseded/logging_utils.py tests/test_logging.py
git commit -m "feat: add shared logging_utils with JsonFormatter and setup_logging"
```

---

## Task 2: Re-export `JsonFormatter` from `server/lifecycle.py`

**Files:**
- Modify: `src/superseded/server/lifecycle.py:1-56`

- [ ] **Step 1: Replace the local `JsonFormatter` with a re-export**

In `src/superseded/server/lifecycle.py`, delete the `_RESERVED_LOG_FIELDS` block, the `import json` line (no longer needed), and the `class JsonFormatter` block. Add an import at the top instead. The final imports + top of file should read:

```python
from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from typing import TYPE_CHECKING

from superseded.logging_utils import JsonFormatter

if TYPE_CHECKING:
    from superseded.server.worker import ReviewWorker

logger = logging.getLogger(__name__)
```

Leave the `ServerLifecycle` class and everything below it unchanged.

- [ ] **Step 2: Verify server still imports and tests pass**

Run: `uv run pytest tests/test_server_lifecycle.py -v 2>/dev/null || uv run pytest tests/ -k lifecycle -v`
Expected: PASS (and no import errors). If no lifecycle test file exists, run `uv run python -c "from superseded.server.lifecycle import JsonFormatter, ServerLifecycle; print('ok')"` and expect `ok`.

- [ ] **Step 3: Lint and format**

Run: `uv run ruff check src/superseded/server/lifecycle.py && uv run ruff format src/superseded/server/lifecycle.py`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add src/superseded/server/lifecycle.py
git commit -m "refactor: re-export JsonFormatter from logging_utils in server lifecycle"
```

---

## Task 3: Add `log_format` / `log_level` to `Config`

**Files:**
- Modify: `src/superseded/config.py:18-33` (`Config` model)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_config_log_defaults():
    from superseded.config import Config

    cfg = Config()
    assert cfg.log_format == "text"
    assert cfg.log_level == "WARNING"


def test_config_log_round_trips(tmp_path):
    from superseded.config import Config, load_config, write_config

    cfg = Config(log_format="json", log_level="INFO")
    path = tmp_path / ".superseded.yaml"
    write_config(cfg, path)
    loaded = load_config(path)
    assert loaded.log_format == "json"
    assert loaded.log_level == "INFO"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k log -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'log_format'`.

- [ ] **Step 3: Add the fields**

In `src/superseded/config.py`, add two fields to the `Config` model (place them right after the `format` field, before `memory`):

```python
    format: str = "table"
    log_format: str = "text"
    log_level: str = "WARNING"
    memory: bool = True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check src/superseded/config.py tests/test_config.py && uv run ruff format src/superseded/config.py tests/test_config.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/superseded/config.py tests/test_config.py
git commit -m "feat: add log_format and log_level to Config"
```

---

## Task 4: Add group-level `--log-format` / `--log-level` CLI options

**Files:**
- Modify: `src/superseded/cli.py` (imports, module constants, `cli` group, every command body)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
@patch("superseded.cli.setup_logging")
@patch("superseded.cli._run_review")
def test_review_calls_setup_logging(mock_review, mock_setup, monkeypatch):
    mock_review.return_value = None
    monkeypatch.delenv("SUPERSEDED_LOG_FORMAT", raising=False)
    monkeypatch.delenv("SUPERSEDED_LOG_LEVEL", raising=False)
    runner = CliRunner()
    result = runner.invoke(cli, ["--log-format", "json", "review", "--pr", "1"])
    assert result.exit_code == 0
    mock_setup.assert_called()
    called_fmt = mock_setup.call_args.args[0]
    assert called_fmt == "json"


@patch("superseded.cli.setup_logging")
def test_log_format_env_overrides_flag(mock_setup, monkeypatch):
    monkeypatch.setenv("SUPERSEDED_LOG_FORMAT", "json")
    runner = CliRunner()
    runner.invoke(cli, ["--log-format", "text", "feedback", "--rules"])
    called_fmt = mock_setup.call_args.args[0]
    assert called_fmt == "json"


@patch("superseded.cli.setup_logging")
def test_log_level_passed_through(mock_setup, monkeypatch):
    monkeypatch.delenv("SUPERSEDED_LOG_LEVEL", raising=False)
    runner = CliRunner()
    runner.invoke(cli, ["--log-level", "DEBUG", "feedback", "--rules"])
    called_level = mock_setup.call_args.args[1]
    assert called_level == "DEBUG"
```

(Note: `feedback --rules` resolves the repo via `gh`; in tests without auth it will error after `setup_logging` is called — that's fine, we only assert `setup_logging` was invoked. The mock on `setup_logging` is what we assert.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k log -v`
Expected: FAIL — `AttributeError: module 'superseded.cli' has no attribute 'setup_logging'` / unknown option `--log-format`.

- [ ] **Step 3: Add imports and resolution helpers**

In `src/superseded/cli.py`:

Add to the imports near the top (with the other superseded imports):

```python
from superseded.logging_utils import setup_logging
```

Add module constants next to `AGENT_ENV`/`MODEL_ENV` (around line 43-45):

```python
LOG_FORMAT_ENV = "SUPERSEDED_LOG_FORMAT"
LOG_LEVEL_ENV = "SUPERSEDED_LOG_LEVEL"
```

Add resolution helpers after `resolve_graph` (after line 80):

```python
def resolve_log_format(flag: str | None, config: Config) -> str:
    return os.environ.get(LOG_FORMAT_ENV) or flag or config.log_format


def resolve_log_level(flag: str | None, config: Config) -> str:
    return os.environ.get(LOG_LEVEL_ENV) or flag or config.log_level
```

- [ ] **Step 4: Add group-level options to the `cli` group**

Replace the existing `cli` group definition:

```python
@click.group()
@click.version_option(version=_VERSION)
@click.option(
    "--log-format",
    "log_format",
    type=click.Choice(["text", "json"]),
    default=None,
    help="Log output format (default: text). Env: SUPERSEDED_LOG_FORMAT.",
)
@click.option(
    "--log-level",
    "log_level",
    default=None,
    help="Log level (e.g. DEBUG/INFO/WARNING). Env: SUPERSEDED_LOG_LEVEL.",
)
@click.pass_context
def cli(ctx: click.Context, log_format: str | None, log_level: str | None) -> None:
    """Superseded — reviews that supersede themselves."""
    ctx.obj = {"log_format": log_format, "log_level": log_level}
```

- [ ] **Step 5: Call `setup_logging` in each command body**

Add `@click.pass_context` to the `review` decorator stack (alongside its existing options) and read `ctx.obj`. Concretely, change the `review` function signature to accept `ctx: click.Context` as its first parameter and add `@click.pass_context` just above `def review(...)`.

At the top of the `review` function body (before the existing validation), insert:

```python
    log_config = load_config(config_path)
    setup_logging(
        resolve_log_format(ctx.obj.get("log_format") if ctx.obj else None, log_config),
        resolve_log_level(ctx.obj.get("log_level") if ctx.obj else None, log_config),
    )
```

**Important:** Do **not** change `_run_review`'s config handling. Existing tests in `tests/test_cli.py` and `tests/test_integration.py` call `_run_review(...)` without a config argument and rely on its internal `load_config(config_path)` reading `.superseded.yaml` from the cwd. Loading config once more in `review` (only to resolve logging) is cheap and idempotent; leave `_run_review` untouched here.

For the `init` command: add `@click.pass_context` and at the top of `_run_init` (or in the `init` command body before delegating) call:

```python
    setup_logging(
        os.environ.get(LOG_FORMAT_ENV) or (ctx.obj.get("log_format") if ctx.obj else None) or "text",
        os.environ.get(LOG_LEVEL_ENV) or (ctx.obj.get("log_level") if ctx.obj else None) or "WARNING",
    )
```

(`init` does not load a `Config`, so use plain defaults.)

For the `feedback` command (it already uses `@click.pass_context` and receives `ctx`): at the top of the `feedback` function body, before any branching, call `setup_logging` with the same env/flag/default logic as `init`.

For the `serve` command: add `@click.pass_context`, and at the top of the `serve` body call `setup_logging` with the env/flag/default logic. The existing `logging.basicConfig(...)` block lower in `serve` will then add its own handler — to avoid duplicate handlers, replace that block's `logging.basicConfig(level=log_level, handlers=[handler])` with:

```python
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))
```

(Import `JsonFormatter` from `superseded.logging_utils` at the top of `serve` instead of from `superseded.server.lifecycle`.)

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k log -v`
Expected: all 3 PASS.

- [ ] **Step 7: Run the full CLI test module to catch regressions**

Run: `uv run pytest tests/test_cli.py -v`
Expected: all PASS (the pre-existing `test_review_requires_pr_or_diff` will be updated in Task 6; if it fails now that's expected and will be fixed there — but it should still pass here because that test invokes `review` with no args and the guard removed only happens in Task 6. If you've already removed the guard, skip this assertion).

- [ ] **Step 8: Lint and format**

Run: `uv run ruff check src/superseded/cli.py tests/test_cli.py && uv run ruff format src/superseded/cli.py tests/test_cli.py`
Expected: clean.

- [ ] **Step 9: Commit**

```bash
git add src/superseded/cli.py tests/test_cli.py
git commit -m "feat: add --log-format/--log-level group options wiring setup_logging"
```

---

## Task 5: Auto-detect git diff in `fetch_diff` + `staged` flag

**Files:**
- Modify: `src/superseded/diff.py:14-72`
- Test: `tests/test_diff.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_diff.py`:

```python
def test_fetch_diff_autodetect_head(monkeypatch):
    from superseded.diff import fetch_diff

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return MagicMock(returncode=0, stdout="diff content\n", stderr="")

    monkeypatch.setattr("superseded.diff.subprocess.run", fake_run)
    out = fetch_diff()
    assert out == "diff content\n"
    assert captured["cmd"][:3] == ["git", "diff", "HEAD"]


def test_fetch_diff_staged_uses_cached(monkeypatch):
    from superseded.diff import fetch_diff

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return MagicMock(returncode=0, stdout="staged diff\n", stderr="")

    monkeypatch.setattr("superseded.diff.subprocess.run", fake_run)
    out = fetch_diff(staged=True)
    assert out == "staged diff\n"
    assert captured["cmd"][:3] == ["git", "diff", "--cached"]


def test_fetch_diff_autodetect_empty_raises(monkeypatch):
    from superseded.diff import fetch_diff

    monkeypatch.setattr(
        "superseded.diff.subprocess.run",
        lambda *a, **kw: MagicMock(returncode=0, stdout="   \n", stderr=""),
    )
    with pytest.raises(ValueError, match=r"no changes detected"):
        fetch_diff()


def test_fetch_diff_staged_ignored_when_diff_range_given(monkeypatch):
    from superseded.diff import fetch_diff

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return MagicMock(returncode=0, stdout="x\n", stderr="")

    monkeypatch.setattr("superseded.diff.subprocess.run", fake_run)
    fetch_diff(diff_range="HEAD~1..HEAD", staged=True)
    assert captured["cmd"] == ["git", "diff", "HEAD~1..HEAD"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_diff.py -k "autodetect or staged" -v`
Expected: FAIL — `TypeError: fetch_diff() got an unexpected keyword argument 'staged'`.

- [ ] **Step 3: Refactor `diff.py`**

Replace the `fetch_diff`, `_fetch_git_diff` region of `src/superseded/diff.py` (lines 14-72) with:

```python
def fetch_diff(
    pr: int | None = None,
    diff_range: str | None = None,
    files: list[str] | None = None,
    staged: bool = False,
) -> str:
    """Fetch a diff.

    ``pr`` and ``diff_range`` are mutually exclusive. ``files`` restricts a
    local ``--diff`` to the given pathspecs (cannot be combined with ``--pr``);
    when only ``files`` are given, the diff defaults to the working tree vs
    ``HEAD``. When nothing is supplied, the working tree is diffed against
    ``HEAD`` (or, with ``staged=True``, the index against ``HEAD``).
    """
    if pr is not None:
        if files:
            raise ValueError("positional FILES cannot be combined with --pr")
        return _fetch_pr_diff(pr)
    if diff_range is not None or files:
        rng = diff_range or "HEAD"
        return _fetch_git_diff(rng, files)
    out = _fetch_raw_diff(["--cached"] if staged else ["HEAD"])
    if not out.strip():
        raise ValueError(
            "no changes detected; stage/commit changes or pass --diff/--pr/FILES"
        )
    return out


def _fetch_pr_diff(pr: int) -> str:
    # ... unchanged ...


def _fetch_git_diff(diff_range: str, files: list[str] | None = None) -> str:
    args: list[str] = [diff_range]
    if files:
        args += ["--", *files]
    return _fetch_raw_diff(args)


def _fetch_raw_diff(args: list[str]) -> str:
    cmd = ["git", "diff", *args]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=DEFAULT_GH_TIMEOUT,
        )
    except FileNotFoundError as err:
        raise RuntimeError("'git' not found on PATH. Install git to use --diff.") from err
    return result.stdout
```

Keep `_fetch_pr_diff` body exactly as it is today.

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_diff.py -k "autodetect or staged" -v`
Expected: all 4 PASS.

- [ ] **Step 5: Run the full diff test module**

Run: `uv run pytest tests/test_diff.py -v`
Expected: all PASS (the existing `test_fetch_git_diff_*` tests must still pass against the refactored helper).

- [ ] **Step 6: Lint and format**

Run: `uv run ruff check src/superseded/diff.py tests/test_diff.py && uv run ruff format src/superseded/diff.py tests/test_diff.py`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/superseded/diff.py tests/test_diff.py
git commit -m "feat: auto-detect git diff HEAD / --cached in fetch_diff"
```

---

## Task 6: Wire `--staged` into `review`; drop the no-args hard error

**Files:**
- Modify: `src/superseded/cli.py` (review options, `_run_review` signature, `fetch_diff` call)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Update the existing no-args test and add `--staged` tests**

In `tests/test_cli.py`, replace `test_review_requires_pr_or_diff` (lines ~30-34) with:

```python
@patch("superseded.cli._run_review")
def test_review_no_args_auto_detects(mock_review):
    mock_review.return_value = None
    runner = CliRunner()
    result = runner.invoke(cli, ["review"])
    assert result.exit_code == 0
    mock_review.assert_called_once()
```

Append:

```python
def test_review_staged_flag_threads_to_fetch_diff(monkeypatch):
    captured: dict = {}

    def fake_run_review(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr("superseded.cli._run_review", fake_run_review)
    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--staged"])
    assert result.exit_code == 0
    assert captured.get("staged") is True


@patch("superseded.cli._run_review")
def test_review_staged_defaults_false(mock_review):
    mock_review.return_value = None
    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--pr", "5"])
    assert result.exit_code == 0
    assert mock_review.call_args.kwargs.get("staged") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "no_args or staged" -v`
Expected: FAIL — `--staged` unknown option / `staged` kwarg missing.

- [ ] **Step 3: Add the `--staged` option to `review`**

In `src/superseded/cli.py`, add this option to the `review` command's decorator stack (e.g. right after `--no-specs`):

```python
@click.option(
    "--staged",
    is_flag=True,
    help="Review staged (cached) changes only; default reviews all uncommitted changes.",
)
```

Add `staged: bool,` to the `review` function parameter list, and pass `staged=staged,` into the `_run_review(...)` call inside `review`.

- [ ] **Step 4: Thread `staged` through `_run_review` and into `fetch_diff`**

In `_run_review`'s signature, add `staged: bool = False,`. In the non-progressive `fetch_diff` call inside `_run_review`, add `staged=staged`:

```python
            diff = fetch_diff(pr=pr, diff_range=diff_range, files=files, staged=staged)
```

Catch the empty-diff `ValueError` alongside the existing `RuntimeError` handler:

```python
        try:
            diff = fetch_diff(pr=pr, diff_range=diff_range, files=files, staged=staged)
        except (RuntimeError, ValueError) as err:
            click.echo(f"Error: {err}", err=True)
            sys.exit(2)
```

- [ ] **Step 5: Remove the no-args hard error**

In the `review` function body, delete the block:

```python
    if pr is None and diff_range is None and not files:
        click.echo(
            "Error: Provide either --pr, --diff, or one or more FILES to review.",
            err=True,
        )
        sys.exit(2)
```

- [ ] **Step 6: Update existing fixed-signature `fetch_diff` mocks**

Two existing mocks in `tests/test_cli.py` declare a fixed parameter list that won't accept the new `staged=` kwarg. Update both:

- Around line 185 (`test_run_review_honors_config_disabled_passes_when_flag_omitted`), change:
  ```python
  monkeypatch.setattr(
      "superseded.cli.fetch_diff",
      lambda pr=None, diff_range=None, files=None: "diff --git a/x.py b/x.py\n",
  )
  ```
  to:
  ```python
  monkeypatch.setattr(
      "superseded.cli.fetch_diff",
      lambda pr=None, diff_range=None, files=None, staged=False: "diff --git a/x.py b/x.py\n",
  )
  ```

- Around line 349 (`fake_fetch_diff`), change its signature from:
  ```python
  def fake_fetch_diff(*, pr, diff_range, files):
  ```
  to:
  ```python
  def fake_fetch_diff(*, pr, diff_range, files, staged=False):
  ```

(The other `fetch_diff` mocks use `lambda **kw:` or `@patch(...)` (MagicMock) and already accept arbitrary kwargs — leave those alone.)

- [ ] **Step 7: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "no_args or staged" -v`
Expected: all 3 PASS.

- [ ] **Step 8: Run the full CLI + integration test modules to confirm no regressions**

Run: `uv run pytest tests/test_cli.py tests/test_integration.py -v`
Expected: all PASS.

- [ ] **Step 9: Lint and format**

Run: `uv run ruff check src/superseded/cli.py tests/test_cli.py && uv run ruff format src/superseded/cli.py tests/test_cli.py`
Expected: clean.

- [ ] **Step 10: Commit**

```bash
git add src/superseded/cli.py tests/test_cli.py
git commit -m "feat: add --staged flag and drop no-args error in review"
```

---

## Task 7: Full verification

- [ ] **Step 1: Run the whole suite**

Run: `uv run pytest tests/ -v`
Expected: all PASS (postgres-marked tests are auto-skipped).

- [ ] **Step 2: Lint and format the whole tree**

Run: `uv run ruff check src/ tests/ && uv run ruff format src/ tests/`
Expected: clean, no changes.

- [ ] **Step 3: Smoke-test the CLI locally**

Run: `uv run superseded review --help`
Expected: `--staged`, `--log-format`, `--log-level` all appear in help text; no traceback.

Run: `uv run superseded --log-format json review --diff HEAD~1..HEAD --format json --passes security 2>/dev/null | head -c 200`
Expected: either JSON findings on stdout or a clean error (this depends on an installed AI CLI; the point is no crash from the new options).

- [ ] **Step 4: Update TODO.md**

In `TODO.md`, change the two completed items from `- [ ]` to `- [x]`:

```markdown
- [x] **Native git diff auto-detect.** ...
- [x] **Structured logging in CLI.** ...
```

- [ ] **Step 5: Commit**

```bash
git add TODO.md
git commit -m "docs: mark CLI logging and git diff auto-detect TODOs complete"
```

---

## Self-Review Notes

- **Spec coverage:** logging extraction (Task 1), server re-export (Task 2), config fields (Task 3), CLI options + per-command wiring + env precedence (Task 4) — covers logging section. Diff auto-detect + staged + empty error (Task 5), CLI `--staged` + no-args removal (Task 6) — covers diff section. Full verification + TODO update (Task 7). All spec sections mapped.
- **Type consistency:** `setup_logging(fmt: str, level: str)` used identically in all call sites. `fetch_diff(..., staged: bool = False)` signature matches `_run_review(staged: bool = False)` matches `review`'s `staged: bool`. `_fetch_raw_diff(args: list[str])` used by both `_fetch_git_diff` and the auto-detect branch.
- **No placeholders:** every code step contains complete, copy-pasteable code.
