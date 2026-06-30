# Progressive PR Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `--pr` reviews progressive — remember the last-reviewed commit per PR so subsequent reviews only cover new commits, with automatic fall-back to full review on rebases/force-pushes/API errors.

**Architecture:** A per-PR commit watermark (`repo, pr_number → head_sha`) stored in `MemoryStore`. The GitHub compare API (`/repos/{o}/{r}/compare/{base}...{head}`) returns both ancestry status and (when `ahead`) the incremental diff patch. CLI and server each keep independent watermarks in their own DB; both reuse the same `MemoryStore` methods and the same status→action mapping. Progressive is on by default; `--full` (CLI) and `config.progressive: false` force full review.

**Tech Stack:** Python 3.14+, pydantic v2, aiosqlite, click, httpx (server), `gh` CLI subprocess (CLI incremental diff). Tests via pytest + pytest-asyncio (asyncio_mode = "auto"). Lint/format via ruff.

**Spec:** `docs/superseded/specs/2026-06-30-progressive-review-design.md`

**Conventions (from AGENTS.md):**
- Every module starts with `from __future__ import annotations`.
- Ruff rule set `E,W,F,I,N,UP,B,SIM,TCH,RUF` (ignores `E501,B008,TC001-003,E741`), line length 100, double quotes, isort `known-first-party = ["superseded"]`.
- Run everything via `uv run` (system python may be 3.13).
- No comments in code unless explicitly part of a requested docstring.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/superseded/config.py` | Add `progressive: bool = True` to `Config`. | Modify |
| `src/superseded/memory/store.py` | `review_watermarks` table, migration, `get_watermark`/`set_watermark`. | Modify |
| `src/superseded/diff.py` | `fetch_pr_head_sha(pr)` helper. | Modify |
| `src/superseded/incremental.py` | `fetch_incremental_diff()` + `IncrementalDiffError`; calls `gh api`. | **Create** |
| `src/superseded/cli.py` | `--full` flag; `_resolve_pr_review_diff` helper; progressive wiring in `_run_review`. | Modify |
| `src/superseded/server/github.py` | `GitHubApp.compare_diff()` (httpx). | Modify |
| `src/superseded/server/worker.py` | Progressive flow in `_run_review_for_job`. | Modify |
| `tests/test_config.py` | Cover `progressive` default. | Modify |
| `tests/test_memory_store.py` | Cover watermark methods + migration. | Modify |
| `tests/test_diff.py` | Cover `fetch_pr_head_sha`. | Modify |
| `tests/test_incremental.py` | Cover `fetch_incremental_diff` status/error paths + argv. | **Create** |
| `tests/test_cli.py` | Cover `--full` and progressive resolution helper. | Modify |
| `tests/test_integration.py` | Cover `_run_review` progressive end-to-end paths. | Modify |
| `tests/test_server_github.py` | Cover `compare_diff`. | Modify |
| `tests/test_server_worker.py` | Cover progressive flow + no-op + fallback. | Modify |

---

## Task 1: Add `progressive` config field

**Files:**
- Modify: `src/superseded/config.py:18-29` (`Config` model)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_config_progressive_defaults_true():
    from superseded.config import Config

    cfg = Config()
    assert cfg.progressive is True


def test_config_progressive_can_be_disabled():
    from superseded.config import Config

    cfg = Config(progressive=False)
    assert cfg.progressive is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_config_progressive_defaults_true -v`
Expected: FAIL with `AttributeError` / unexpected keyword `progressive`.

- [ ] **Step 3: Add the field**

In `src/superseded/config.py`, add `progressive: bool = True` to the `Config` model after the `graph` field (line 29):

```python
    graph: bool = True
    progressive: bool = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (all config tests, including the two new ones).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check src/superseded/config.py tests/test_config.py
uv run ruff format src/superseded/config.py tests/test_config.py
git add src/superseded/config.py tests/test_config.py
git commit -m "feat(config): add progressive review flag (default true)"
```

---

## Task 2: Watermark storage in `MemoryStore`

**Files:**
- Modify: `src/superseded/memory/store.py` (SCHEMA, `_migrate`, two new methods)
- Test: `tests/test_memory_store.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_memory_store.py`:

```python
async def test_get_watermark_returns_none_when_absent(tmp_path):
    from superseded.memory.store import MemoryStore

    store = MemoryStore(db_path=tmp_path / "m.db")
    await store.init()
    assert await store.get_watermark("owner/repo", 7) is None


async def test_set_then_get_watermark(tmp_path):
    from superseded.memory.store import MemoryStore

    store = MemoryStore(db_path=tmp_path / "m.db")
    await store.init()
    await store.set_watermark("owner/repo", 7, "abc123")
    assert await store.get_watermark("owner/repo", 7) == "abc123"


async def test_set_watermark_replaces_existing(tmp_path):
    from superseded.memory.store import MemoryStore

    store = MemoryStore(db_path=tmp_path / "m.db")
    await store.init()
    await store.set_watermark("owner/repo", 7, "abc123")
    await store.set_watermark("owner/repo", 7, "def456")
    assert await store.get_watermark("owner/repo", 7) == "def456"


async def test_watermark_keys_per_repo_and_pr(tmp_path):
    from superseded.memory.store import MemoryStore

    store = MemoryStore(db_path=tmp_path / "m.db")
    await store.init()
    await store.set_watermark("owner/repo", 7, "aaa")
    await store.set_watermark("owner/repo", 8, "bbb")
    await store.set_watermark("other/repo", 7, "ccc")
    assert await store.get_watermark("owner/repo", 7) == "aaa"
    assert await store.get_watermark("owner/repo", 8) == "bbb"
    assert await store.get_watermark("other/repo", 7) == "ccc"


async def test_watermark_table_added_by_migration(tmp_path):
    """An existing DB (created without the watermark table) must gain it via _migrate."""
    import aiosqlite

    from superseded.memory.store import MemoryStore

    db_path = tmp_path / "old.db"
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(
            "CREATE TABLE findings (id TEXT PRIMARY KEY);"
            "CREATE TABLE feedback (id INTEGER PRIMARY KEY AUTOINCREMENT);"
            "CREATE TABLE installations (id INTEGER PRIMARY KEY AUTOINCREMENT);"
        )
        await db.commit()

    store = MemoryStore(db_path=db_path)
    await store.init()
    await store.set_watermark("owner/repo", 1, "deadbeef")
    assert await store.get_watermark("owner/repo", 1) == "deadbeef"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_memory_store.py -k watermark -v`
Expected: FAIL with `AttributeError: 'MemoryStore' object has no attribute 'get_watermark'` (and the migration test may fail on the missing table).

- [ ] **Step 3: Add the table to `SCHEMA`**

In `src/superseded/memory/store.py`, append to the `SCHEMA` string (after the `installations` table block, before the closing `"""`):

```sql

CREATE TABLE IF NOT EXISTS review_watermarks (
    repo        TEXT    NOT NULL,
    pr_number   INTEGER NOT NULL,
    head_sha    TEXT    NOT NULL,
    reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (repo, pr_number)
);
```

- [ ] **Step 4: Add migration for existing DBs**

In `_migrate()` (after the `reasoning` column block, before the end of the method), add an idempotent create:

```python
        await db.execute(
            "CREATE TABLE IF NOT EXISTS review_watermarks ("
            "repo TEXT NOT NULL, "
            "pr_number INTEGER NOT NULL, "
            "head_sha TEXT NOT NULL, "
            "reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY (repo, pr_number))"
        )
```

- [ ] **Step 5: Add the two methods**

Add to `MemoryStore` (after `remove_installation`, at the end of the class):

```python
    async def get_watermark(self, repo: str, pr_number: int) -> str | None:
        async with self._db() as db:
            cursor = await db.execute(
                "SELECT head_sha FROM review_watermarks WHERE repo = ? AND pr_number = ?",
                (repo, pr_number),
            )
            row = await cursor.fetchone()
            return row[0] if row is not None else None

    async def set_watermark(self, repo: str, pr_number: int, head_sha: str) -> None:
        async with self._db() as db:
            await db.execute(
                "INSERT INTO review_watermarks (repo, pr_number, head_sha) VALUES (?, ?, ?) "
                "ON CONFLICT(repo, pr_number) DO UPDATE SET head_sha = excluded.head_sha",
                (repo, pr_number, head_sha),
            )
            await db.commit()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_memory_store.py -v`
Expected: PASS (all existing + 5 new tests).

- [ ] **Step 7: Lint + commit**

```bash
uv run ruff check src/superseded/memory/store.py tests/test_memory_store.py
uv run ruff format src/superseded/memory/store.py tests/test_memory_store.py
git add src/superseded/memory/store.py tests/test_memory_store.py
git commit -m "feat(memory): per-PR review watermark storage"
```

---

## Task 3: `fetch_pr_head_sha` helper in `diff.py`

**Files:**
- Modify: `src/superseded/diff.py` (add function after `fetch_pr_description`)
- Test: `tests/test_diff.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_diff.py`:

```python
def test_fetch_pr_head_sha_returns_oid(patch_subprocess):
    from superseded import diff as diff_mod

    patch_subprocess.return_value = MagicMock(
        stdout="abc123deadbeef\n", returncode=0
    )
    sha = diff_mod.fetch_pr_head_sha(42)
    assert sha == "abc123deadbeef"
    args = patch_subprocess.return_value  # not used; verify call below
    del args
    patch_subprocess.assert_called_once()
    cmd = patch_subprocess.call_args.args[0]
    assert cmd[:5] == ["gh", "pr", "view", "42", "--json"]


def test_fetch_pr_head_sha_strips_whitespace(patch_subprocess):
    from superseded import diff as diff_mod

    patch_subprocess.return_value = MagicMock(
        stdout="  abc123  \n", returncode=0
    )
    assert diff_mod.fetch_pr_head_sha(1) == "abc123"


def test_fetch_pr_head_sha_raises_on_gh_failure(patch_subprocess):
    import pytest

    from superseded import diff as diff_mod

    patch_subprocess.side_effect = subprocess.CalledProcessError(
        1, "gh", stderr="not found"
    )
    with pytest.raises(RuntimeError, match="gh pr view 9 failed"):
        diff_mod.fetch_pr_head_sha(9)
```

If `tests/test_diff.py` does not already define a `patch_subprocess` fixture and import `MagicMock`/`subprocess`, add this fixture and the imports near the top of the file:

```python
import subprocess
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def patch_subprocess(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("superseded.diff.subprocess.run", mock)
    return mock
```

(If a comparable fixture already exists with a different name, reuse it and adjust the three tests to use that name instead of `patch_subprocess`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_diff.py -k fetch_pr_head_sha -v`
Expected: FAIL with `AttributeError: module 'superseded.diff' has no attribute 'fetch_pr_head_sha'`.

- [ ] **Step 3: Implement the helper**

In `src/superseded/diff.py`, add after `fetch_pr_description` (before the regex constants block):

```python
def fetch_pr_head_sha(pr: int) -> str:
    """Return the current HEAD SHA of PR ``pr`` via ``gh pr view``."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr), "--json", "headRefOid", "-q", ".headRefOid"],
            capture_output=True,
            text=True,
            check=True,
            timeout=DEFAULT_GH_TIMEOUT,
        )
    except FileNotFoundError as err:
        raise RuntimeError(
            "'gh' CLI not found on PATH. Install it: https://cli.github.com/"
        ) from err
    except subprocess.CalledProcessError as err:
        detail = (err.stderr or "").strip()
        msg = f"'gh pr view {pr}' failed (exit {err.returncode})"
        if detail:
            msg += f": {detail}"
        raise RuntimeError(msg) from err
    return result.stdout.strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_diff.py -v`
Expected: PASS (all existing + 3 new tests).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check src/superseded/diff.py tests/test_diff.py
uv run ruff format src/superseded/diff.py tests/test_diff.py
git add src/superseded/diff.py tests/test_diff.py
git commit -m "feat(diff): fetch_pr_head_sha helper for progressive review"
```

---

## Task 4: `incremental.py` — compare-API diff fetcher

**Files:**
- Create: `src/superseded/incremental.py`
- Test: `tests/test_incremental.py`

The function makes two `gh api` calls (one JSON for status, one diff-body when ahead). Tests mock `superseded.incremental.subprocess.run`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_incremental.py`:

```python
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

from superseded.incremental import IncrementalDiffError, fetch_incremental_diff


@pytest.fixture
def mock_run(monkeypatch):
    m = MagicMock()
    monkeypatch.setattr("superseded.incremental.subprocess.run", m)
    return m


def _ok(stdout: str):
    return MagicMock(stdout=stdout, returncode=0)


def test_ahead_returns_patch(mock_run):
    mock_run.side_effect = [
        _ok('{"status": "ahead", "total_commits": 3}'),
        _ok("diff --git a/x.py b/x.py\n+new\n"),
    ]
    diff, status = fetch_incremental_diff("owner", "repo", "base", "head")
    assert status == "ahead"
    assert diff == "diff --git a/x.py b/x.py\n+new\n"


def test_identical_returns_none_diff(mock_run):
    mock_run.return_value = _ok('{"status": "identical", "total_commits": 0}')
    diff, status = fetch_incremental_diff("owner", "repo", "base", "head")
    assert status == "identical"
    assert diff is None


def test_diverged_returns_none_diff(mock_run):
    mock_run.return_value = _ok('{"status": "diverged", "total_commits": 5}')
    diff, status = fetch_incremental_diff("owner", "repo", "base", "head")
    assert status == "diverged"
    assert diff is None


def test_behind_is_normalized_to_diverged(mock_run):
    mock_run.return_value = _ok('{"status": "behind", "total_commits": 0}')
    diff, status = fetch_incremental_diff("owner", "repo", "base", "head")
    assert status == "diverged"
    assert diff is None


def test_called_process_error_raises_incremental_error(mock_run):
    mock_run.side_effect = subprocess.CalledProcessError(
        1, "gh", stderr="boom"
    )
    with pytest.raises(IncrementalDiffError):
        fetch_incremental_diff("owner", "repo", "base", "head")


def test_file_not_found_raises_incremental_error(mock_run):
    mock_run.side_effect = FileNotFoundError("gh")
    with pytest.raises(IncrementalDiffError):
        fetch_incremental_diff("owner", "repo", "base", "head")


def test_status_call_uses_compare_endpoint(mock_run):
    mock_run.side_effect = [
        _ok('{"status": "ahead", "total_commits": 1}'),
        _ok("patch"),
    ]
    fetch_incremental_diff("owner", "repo", "aaa", "bbb")
    status_cmd = mock_run.call_args_list[0].args[0]
    assert status_cmd[:3] == ["gh", "api", "repos/owner/repo/compare/aaa...bbb"]


def test_diff_call_uses_diff_accept_header(mock_run):
    mock_run.side_effect = [
        _ok('{"status": "ahead", "total_commits": 1}'),
        _ok("patch"),
    ]
    fetch_incremental_diff("owner", "repo", "aaa", "bbb")
    diff_call = mock_run.call_args_list[1]
    diff_cmd = diff_call.args[0]
    assert "repos/owner/repo/compare/aaa...bbb" in diff_cmd
    assert "-H" in diff_cmd
    accept_idx = diff_cmd.index("-H") + 1
    assert diff_cmd[accept_idx] == "Accept: application/vnd.github.v3.diff"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_incremental.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'superseded.incremental'`.

- [ ] **Step 3: Implement the module**

Create `src/superseded/incremental.py`:

```python
from __future__ import annotations

import json
import subprocess

DEFAULT_TIMEOUT = 30
DIFF_ACCEPT = "application/vnd.github.v3.diff"


class IncrementalDiffError(RuntimeError):
    """Raised when the GitHub compare API call fails.

    Callers treat this as a signal to fall back to a full review.
    """


def fetch_incremental_diff(
    owner: str, repo: str, base_sha: str, head_sha: str
) -> tuple[str | None, str]:
    """Fetch the incremental diff between two commits via the GitHub compare API.

    Returns ``(diff, status)`` where ``status`` is one of ``"ahead"``,
    ``"identical"``, or ``"diverged"``. ``diff`` is the patch string when
    ``status == "ahead"`` and ``None`` otherwise. ``"behind"`` from the API is
    normalized to ``"diverged"``.

    Raises ``IncrementalDiffError`` on any ``gh``/network failure; callers fall
    back to a full review.
    """
    endpoint = f"repos/{owner}/{repo}/compare/{base_sha}...{head_sha}"
    try:
        status_result = subprocess.run(
            ["gh", "api", endpoint],
            capture_output=True,
            text=True,
            check=True,
            timeout=DEFAULT_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as err:
        raise IncrementalDiffError(f"gh api {endpoint} failed: {err}") from err

    try:
        payload = json.loads(status_result.stdout)
    except json.JSONDecodeError as err:
        raise IncrementalDiffError(f"compare response was not JSON: {err}") from err

    status = payload.get("status", "diverged")
    if status == "behind":
        status = "diverged"
    if status != "ahead":
        return None, status

    try:
        diff_result = subprocess.run(
            ["gh", "api", endpoint, "-H", f"Accept: {DIFF_ACCEPT}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=DEFAULT_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as err:
        raise IncrementalDiffError(f"gh api diff {endpoint} failed: {err}") from err
    return diff_result.stdout, "ahead"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_incremental.py -v`
Expected: PASS (all 8 tests).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check src/superseded/incremental.py tests/test_incremental.py
uv run ruff format src/superseded/incremental.py tests/test_incremental.py
git add src/superseded/incremental.py tests/test_incremental.py
git commit -m "feat(incremental): GitHub compare-API incremental diff fetcher"
```

---

## Task 5: CLI progressive wiring (`--full` + `_resolve_pr_review_diff`)

**Files:**
- Modify: `src/superseded/cli.py`:
  - imports (add `fetch_pr_head_sha`, `fetch_incremental_diff`, `IncrementalDiffError`)
  - `review` click options (add `--full`)
  - `review()` signature + forwarding into `_run_review`
  - `_run_review()` signature (add `full: bool = False`) + body
  - new helper `_resolve_pr_review_diff`
- Test: `tests/test_cli.py` (helper unit), `tests/test_integration.py` (end-to-end)

This task has two test sets: (A) the pure helper, (B) `_run_review` integration paths.

### Part A — the `_resolve_pr_review_diff` helper

- [ ] **Step A1: Write failing tests for the helper**

Append to `tests/test_cli.py`:

```python
from unittest.mock import MagicMock


def _fake_store_with_watermark(wm: str | None):
    store = MagicMock()
    store.get_watermark = MagicMock(return_value=_async_return(wm))
    return store


def _async_return(value):
    import asyncio

    fut: asyncio.Future = asyncio.get_event_loop().create_future() if False else None
    # Simpler: return an awaitable via a small coroutine wrapper.

    async def _coro():
        return value

    return _coro()
```

If the above Future dance is awkward, use this simpler fixture pattern instead (delete the `_async_return` helper and use this):

```python
import asyncio


def _fake_store_with_watermark(wm: str | None):
    store = MagicMock()

    async def _get(repo, pr):
        return wm

    store.get_watermark = _get
    return store
```

Then the helper tests:

```python
def test_resolve_no_watermark_uses_full_diff(monkeypatch):
    from superseded.cli import _resolve_pr_review_diff

    monkeypatch.setattr("superseded.cli.fetch_pr_head_sha", lambda pr: "head")
    monkeypatch.setattr("superseded.cli.fetch_diff", lambda **kw: "FULLDIFF")
    monkeypatch.setattr("superseded.cli.fetch_incremental_diff", lambda *a: ("", "ahead"))
    store = _fake_store_with_watermark(None)

    diff, mode, head = _resolve_pr_review_diff(pr=1, repo="o/r", store=store, full=False)
    assert diff == "FULLDIFF"
    assert mode == "full"
    assert head == "head"


def test_resolve_full_flag_skips_incremental(monkeypatch):
    from superseded.cli import _resolve_pr_review_diff

    monkeypatch.setattr("superseded.cli.fetch_pr_head_sha", lambda pr: "head")
    monkeypatch.setattr("superseded.cli.fetch_diff", lambda **kw: "FULLDIFF")
    inc = MagicMock()
    monkeypatch.setattr("superseded.cli.fetch_incremental_diff", inc)
    store = _fake_store_with_watermark("base")

    diff, mode, head = _resolve_pr_review_diff(pr=1, repo="o/r", store=store, full=True)
    assert diff == "FULLDIFF"
    assert mode == "full"
    inc.assert_not_called()


def test_resolve_ahead_uses_incremental(monkeypatch):
    from superseded.cli import _resolve_pr_review_diff

    monkeypatch.setattr("superseded.cli.fetch_pr_head_sha", lambda pr: "head")
    full = MagicMock()
    monkeypatch.setattr("superseded.cli.fetch_diff", full)
    monkeypatch.setattr(
        "superseded.cli.fetch_incremental_diff", lambda *a: ("INCDIFF", "ahead")
    )
    store = _fake_store_with_watermark("base")

    diff, mode, head = _resolve_pr_review_diff(pr=1, repo="o/r", store=store, full=False)
    assert diff == "INCDIFF"
    assert mode == "incremental"
    full.assert_not_called()


def test_resolve_identical_returns_noop(monkeypatch):
    from superseded.cli import _resolve_pr_review_diff

    monkeypatch.setattr("superseded.cli.fetch_pr_head_sha", lambda pr: "head")
    monkeypatch.setattr("superseded.cli.fetch_diff", lambda **kw: "FULLDIFF")
    monkeypatch.setattr(
        "superseded.cli.fetch_incremental_diff", lambda *a: (None, "identical")
    )
    store = _fake_store_with_watermark("base")

    diff, mode, head = _resolve_pr_review_diff(pr=1, repo="o/r", store=store, full=False)
    assert diff is None
    assert mode == "noop"


def test_resolve_diverged_falls_back_to_full(monkeypatch):
    from superseded.cli import _resolve_pr_review_diff

    monkeypatch.setattr("superseded.cli.fetch_pr_head_sha", lambda pr: "head")
    monkeypatch.setattr("superseded.cli.fetch_diff", lambda **kw: "FULLDIFF")
    monkeypatch.setattr(
        "superseded.cli.fetch_incremental_diff", lambda *a: (None, "diverged")
    )
    store = _fake_store_with_watermark("base")

    diff, mode, head = _resolve_pr_review_diff(pr=1, repo="o/r", store=store, full=False)
    assert diff == "FULLDIFF"
    assert mode == "fallback"


def test_resolve_incremental_error_falls_back(monkeypatch):
    from superseded.cli import _resolve_pr_review_diff
    from superseded.incremental import IncrementalDiffError

    monkeypatch.setattr("superseded.cli.fetch_pr_head_sha", lambda pr: "head")
    monkeypatch.setattr("superseded.cli.fetch_diff", lambda **kw: "FULLDIFF")

    def _boom(*a):
        raise IncrementalDiffError("nope")

    monkeypatch.setattr("superseded.cli.fetch_incremental_diff", _boom)
    store = _fake_store_with_watermark("base")

    diff, mode, head = _resolve_pr_review_diff(pr=1, repo="o/r", store=store, full=False)
    assert diff == "FULLDIFF"
    assert mode == "fallback"
```

- [ ] **Step A2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k resolve -v`
Expected: FAIL with `ImportError: cannot import name '_resolve_pr_review_diff'`.

- [ ] **Step A3: Add imports to `cli.py`**

In `src/superseded/cli.py`, extend the existing `from superseded.diff import (...)` block to include `fetch_pr_head_sha`, and add a new import for the incremental module. After the existing diff import:

```python
from superseded.diff import (
    fetch_diff,
    fetch_pr_description,
    fetch_pr_head_sha,
    repo_root,
)
from superseded.incremental import IncrementalDiffError, fetch_incremental_diff
```

- [ ] **Step A4: Implement the helper**

Add to `src/superseded/cli.py` (near the other module-level helpers like `_parse_passes`):

```python
def _resolve_pr_review_diff(
    pr: int,
    repo: str,
    store: MemoryStore,
    full: bool,
) -> tuple[str | None, str, str]:
    """Resolve the diff for a ``--pr`` review, applying progressive logic.

    Returns ``(diff, mode, head_sha)``:
      mode "noop"        -> diff is None; no new commits; caller emits empty result
      mode "full"        -> full PR diff (first review, --full, or no watermark)
      mode "incremental" -> diff since the watermark
      mode "fallback"    -> full diff after a stale watermark or compare-API error

    The caller writes ``store.set_watermark(repo, pr, head_sha)`` after a
    successful review for every mode except "noop".
    """
    head_sha = fetch_pr_head_sha(pr)
    watermark = asyncio.run(store.get_watermark(repo, pr))

    if watermark is None or full:
        return fetch_diff(pr=pr), "full", head_sha

    owner, _, name = repo.partition("/")
    try:
        status, patch = fetch_incremental_diff(owner, name, watermark, head_sha)
    except IncrementalDiffError:
        _status(f"watermark {watermark[:7]} unreachable; falling back to full review")
        return fetch_diff(pr=pr), "fallback", head_sha

    if status == "identical":
        return None, "noop", head_sha
    if status == "ahead":
        _status(f"Reviewing new commits since {watermark[:7]}...")
        return patch, "incremental", head_sha

    _status(f"watermark {watermark[:7]} no longer an ancestor; falling back to full review")
    return fetch_diff(pr=pr), "fallback", head_sha
```

- [ ] **Step A5: Run helper tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k resolve -v`
Expected: PASS (all 6 helper tests).

### Part B — wire `_run_review` + `--full` flag

- [ ] **Step B1: Write failing integration tests**

Append to `tests/test_integration.py`. These extend the existing `FakeStore` (add watermark support) and mock the new helper functions.

First, extend `FakeStore` (add inside the `FakeStore` class body):

```python
    def __init__(self):
        super().__init__() if False else None
        self.findings = {}
        self.comment_ids = {}
        self.feedback = []
        self._dismissed = set()
        self.dismissed_calls = 0
        self.watermarks = {}
        self.set_watermark_calls = []

    async def get_watermark(self, repo, pr_number):
        return self.watermarks.get((repo, pr_number))

    async def set_watermark(self, repo, pr_number, head_sha):
        self.watermarks[(repo, pr_number)] = head_sha
        self.set_watermark_calls.append((repo, pr_number, head_sha))
```

(If `FakeStore.__init__` already exists verbatim, replace its body with the above so it also initializes `watermarks` and `set_watermark_calls`. Do not add a second `__init__`.)

Now the integration tests:

```python
@patch("superseded.cli._resolve_pr_review_diff")
@patch("superseded.cli.MemoryStore")
@patch("superseded.cli.current_repo")
@patch("superseded.cli.ReviewEngine")
@patch("superseded.context.gathering.compute_file_context")
@patch("superseded.cli.fetch_pr_description")
def test_review_progressive_writes_watermark_after_success(
    mock_desc, mock_ctx, mock_engine_cls, mock_repo, mock_store_cls, mock_resolve
):
    mock_desc.return_value = None
    mock_ctx.return_value = "ctx"
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    mock_engine.review.return_value = ReviewResult(findings=[])
    mock_engine.agent.is_available.return_value = True
    mock_repo.return_value = "owner/repo"
    store = FakeStore()
    mock_store_cls.return_value = store
    mock_resolve.return_value = ("DIFF", "incremental", "headsha")

    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--pr", "5"])

    assert result.exit_code == 0, result.output
    mock_resolve.assert_called_once()
    assert ("owner/repo", 5, "headsha") in store.set_watermark_calls


@patch("superseded.cli._resolve_pr_review_diff")
@patch("superseded.cli.MemoryStore")
@patch("superseded.cli.current_repo")
@patch("superseded.cli.ReviewEngine")
@patch("superseded.context.gathering.compute_file_context")
@patch("superseded.cli.fetch_pr_description")
def test_review_noop_when_identical(
    mock_desc, mock_ctx, mock_engine_cls, mock_repo, mock_store_cls, mock_resolve
):
    mock_desc.return_value = None
    mock_ctx.return_value = "ctx"
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    mock_engine.agent.is_available.return_value = True
    mock_repo.return_value = "owner/repo"
    store = FakeStore()
    mock_store_cls.return_value = store
    mock_resolve.return_value = (None, "noop", "headsha")

    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--pr", "5", "--format", "json"])

    assert result.exit_code == 0, result.output
    mock_engine.review.assert_not_called()
    assert store.set_watermark_calls == []


@patch("superseded.cli._resolve_pr_review_diff")
@patch("superseded.cli.MemoryStore")
@patch("superseded.cli.current_repo")
@patch("superseded.cli.ReviewEngine")
@patch("superseded.context.gathering.compute_file_context")
@patch("superseded.cli.fetch_pr_description")
def test_review_engine_failure_does_not_advance_watermark(
    mock_desc, mock_ctx, mock_engine_cls, mock_repo, mock_store_cls, mock_resolve
):
    mock_desc.return_value = None
    mock_ctx.return_value = "ctx"
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    mock_engine.agent.is_available.return_value = True
    mock_engine.review.side_effect = RuntimeError("agent crashed")
    mock_repo.return_value = "owner/repo"
    store = FakeStore()
    mock_store_cls.return_value = store
    mock_resolve.return_value = ("DIFF", "incremental", "headsha")

    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--pr", "5"])

    assert result.exit_code == 1, result.output
    assert store.set_watermark_calls == []


@patch("superseded.cli.fetch_pr_head_sha")
@patch("superseded.cli.fetch_incremental_diff")
@patch("superseded.cli.fetch_diff")
@patch("superseded.cli.MemoryStore")
@patch("superseded.cli.current_repo")
@patch("superseded.cli.ReviewEngine")
@patch("superseded.context.gathering.compute_file_context")
@patch("superseded.cli.fetch_pr_description")
def test_review_full_flag_skips_resolve_and_advances(
    mock_desc, mock_ctx, mock_engine_cls, mock_repo, mock_store_cls,
    mock_fetch_diff, mock_fetch_inc, mock_fetch_head,
):
    mock_desc.return_value = None
    mock_ctx.return_value = "ctx"
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    mock_engine.review.return_value = ReviewResult(findings=[])
    mock_engine.agent.is_available.return_value = True
    mock_repo.return_value = "owner/repo"
    store = FakeStore()
    mock_store_cls.return_value = store
    mock_fetch_diff.return_value = "FULLDIFF"
    mock_fetch_head.return_value = "headsha"
    mock_fetch_inc.return_value = ("INCDIFF", "ahead")

    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--pr", "5", "--full"])

    assert result.exit_code == 0, result.output
    mock_fetch_inc.assert_not_called()
    assert ("owner/repo", 5, "headsha") in store.set_watermark_calls
```

- [ ] **Step B2: Run tests to verify they fail**

Run: `uv run pytest tests/test_integration.py -k "progressive or noop or engine_failure or full_flag" -v`
Expected: FAIL — `_resolve_pr_review_diff` exists but isn't wired into `review`/`_run_review`; `--full` flag unknown; watermark never written.

- [ ] **Step B3: Add the `--full` flag to the `review` command**

In `src/superseded/cli.py`, add this option among the other `@click.option` decorators on `review` (after the `--no-memory` option is fine):

```python
@click.option("--full", "full_review", is_flag=True, help="Force a full review (ignore progressive watermark)")
```

Add `full_review: bool` to the `review()` function signature (after `files: tuple[str, ...]`):

```python
def review(
    pr: int | None,
    diff_range: str | None,
    agent: str | None,
    model: str | None,
    output_format: str | None,
    post: bool,
    passes: str | None,
    timeout: int | None,
    config_path: Path | None,
    no_memory: bool,
    no_static: bool,
    no_usage: bool,
    no_conventions: bool,
    no_specs: bool,
    graph: bool | None,
    full_review: bool,
    files: tuple[str, ...],
) -> None:
```

Forward it in the `_run_review(...)` call inside `review()`:

```python
    _run_review(
        pr=pr,
        diff_range=diff_range,
        agent=agent,
        model=model,
        output_format=output_format,
        post=post,
        passes=pass_list,
        timeout=timeout,
        config_path=config_path,
        no_memory=no_memory,
        no_static=no_static,
        no_usage=no_usage,
        no_conventions=no_conventions,
        no_specs=no_specs,
        graph=graph,
        full=full_review,
        files=list(files) or None,
    )
```

- [ ] **Step B4: Wire progressive logic into `_run_review`**

In `src/superseded/cli.py`, change the `_run_review` signature to accept `full: bool = False`:

```python
def _run_review(
    pr: int | None,
    diff_range: str | None,
    agent: str | None,
    model: str | None,
    output_format: str | None,
    post: bool,
    passes: list[str] | None,
    *,
    timeout: int | None = None,
    config_path: Path | None = None,
    no_memory: bool = False,
    no_static: bool = False,
    no_usage: bool = False,
    no_conventions: bool = False,
    no_specs: bool = False,
    graph: bool | None = None,
    full: bool = False,
    files: list[str] | None = None,
) -> None:
```

Then restructure the diff + store portion of `_run_review`. Replace the existing block:

```python
    _status("Fetching diff...")
    try:
        diff = fetch_diff(pr=pr, diff_range=diff_range, files=files)
    except RuntimeError as err:
        click.echo(f"Error: {err}", err=True)
        sys.exit(1)
```

…and the later store-creation block:

```python
    repo = current_repo()
    memory_context: str | None = None
    store: MemoryStore | None = None
    if config.memory and not no_memory and repo:
        store = MemoryStore()
        dismissed = asyncio.run(_load_dismissed(store, repo))
        memory_context = format_memory_context(dismissed)
```

…with a unified progressive-aware version. The new ordering is: resolve repo + store first (so progressive can use it), then resolve the diff:

```python
    repo = current_repo()
    memory_enabled = config.memory and not no_memory and repo is not None
    progressive_active = memory_enabled and config.progressive and pr is not None

    store: MemoryStore | None = None
    if memory_enabled:
        store = MemoryStore()

    head_sha: str | None = None

    if progressive_active:
        assert repo is not None  # narrowed by memory_enabled
        assert store is not None
        try:
            diff, mode, head_sha = _resolve_pr_review_diff(
                pr=pr, repo=repo, store=store, full=full
            )
        except RuntimeError as err:
            click.echo(f"Error: {err}", err=True)
            sys.exit(1)
        if mode == "noop":
            _status(f"No new commits since last review.")
            empty = ReviewResult(findings=[], warnings=[])
            if fmt == "json":
                click.echo(format_json(empty))
            elif fmt == "markdown":
                click.echo(format_markdown(empty))
            else:
                click.echo(format_table(empty))
            return
        _status("Gathering context...")
    elif pr is not None and not progressive_active and memory_enabled:
        # PR review with memory but progressive disabled -> normal full fetch.
        _status("Fetching diff...")
        try:
            diff = fetch_diff(pr=pr)
        except RuntimeError as err:
            click.echo(f"Error: {err}", err=True)
            sys.exit(1)
        _status("Gathering context...")
    else:
        if pr is not None and not memory_enabled:
            _status("memory disabled; running full review (progressive review needs memory)")
        _status("Fetching diff...")
        try:
            diff = fetch_diff(pr=pr, diff_range=diff_range, files=files)
        except RuntimeError as err:
            click.echo(f"Error: {err}", err=True)
            sys.exit(1)
        _status("Gathering context...")
```

(Note: `fmt` is assigned later in the existing function from `output_format or config.format`. Move that assignment ABOVE this block so `fmt` is in scope. Locate `fmt = output_format or config.format` and move it to just after `config = load_config(config_path)` near the top of `_run_review`.)

Then, after the existing `if config.memory and not no_memory and repo:` block that builds `memory_context`, ensure the dismissed-findings load still runs. Replace the old combined store-creation block with:

```python
    memory_context: str | None = None
    if store is not None and repo:
        dismissed = asyncio.run(_load_dismissed(store, repo))
        memory_context = format_memory_context(dismissed)
```

Finally, after findings are persisted (the existing `if store is not None and repo:` block that calls `_persist_findings`), add watermark advancement — but ONLY on the success path (this code runs after the engine call succeeded; engine errors exit earlier via `sys.exit(1)`):

```python
    if store is not None and repo:
        asyncio.run(_persist_findings(store, result, repo))

    if (
        head_sha is not None
        and store is not None
        and repo is not None
        and pr is not None
    ):
        asyncio.run(_set_watermark(store, repo, pr, head_sha))
```

Add the helper near `_persist_findings`:

```python
async def _set_watermark(store: MemoryStore, repo: str, pr: int, head_sha: str) -> None:
    async with store:
        await store.set_watermark(repo, pr, head_sha)
```

- [ ] **Step B5: Run integration tests to verify they pass**

Run: `uv run pytest tests/test_integration.py -v`
Expected: PASS (all existing tests still pass, plus the 4 new ones).

If an existing test breaks because it mocked `superseded.cli.fetch_diff` for a `--pr` invocation and now expects progressive code to run instead, check that test: it should still work because when `config.memory` is True (default) and `current_repo` returns a value, progressive IS active and `_resolve_pr_review_diff` is invoked instead of `fetch_diff`. For those legacy tests, either (a) patch `superseded.cli._resolve_pr_review_diff` to return `("diff", "full", None)` so the watermark-write branch is skipped (head_sha is None), or (b) add `--no-memory` to the invocation. Prefer (a) for tests that exercise memory behaviour, (b) for tests that don't care about memory. Update each failing test minimally.

- [ ] **Step B6: Lint + commit**

```bash
uv run ruff check src/superseded/cli.py tests/test_cli.py tests/test_integration.py
uv run ruff format src/superseded/cli.py tests/test_cli.py tests/test_integration.py
git add src/superseded/cli.py tests/test_cli.py tests/test_integration.py
git commit -m "feat(cli): progressive --pr review with --full override"
```

---

## Task 6: `GitHubApp.compare_diff` (server-side)

**Files:**
- Modify: `src/superseded/server/github.py` (add method after `fetch_pr_description`)
- Test: `tests/test_server_github.py`

The server uses `httpx` (not `subprocess`/`gh`). `compare_diff` makes up to two requests: JSON for status, diff body when ahead. Tests mock `httpx.AsyncClient`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server_github.py`. Adjust imports if `respx`/`httpx.MockTransport` isn't already used; prefer `httpx.MockTransport` (no extra dep). Check the file first — if tests already patch `httpx.AsyncClient`, match that style. Otherwise add:

```python
import httpx
import pytest

from superseded.server.github import GitHubApp


def _app():
    import tempfile
    from pathlib import Path

    key = tempfile.NamedTemporaryFile(suffix=".pem", delete=False)
    key.write(b"dummy")
    key.close()
    return GitHubApp(app_id=1, private_key_path=Path(key.name), webhook_secret="x")


def _transport(routes):
    def handler(request):
        path = request.url.path
        for predicate, response in routes:
            if predicate(request):
                return response
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_compare_diff_ahead_returns_patch():
    app = _app()
    routes = []

    def is_json(req):
        return req.url.path == "/repos/o/r/compare/a...b" and req.headers.get("accept", "").startswith("application/vnd.github+json")

    def is_diff(req):
        return req.url.path == "/repos/o/r/compare/a...b" and "v3.diff" in req.headers.get("accept", "")

    routes.append((is_json, httpx.Response(200, json={"status": "ahead"})))
    routes.append((is_diff, httpx.Response(200, text="PATCH")))
    original = httpx.AsyncClient

    def _client(*a, **kw):
        kw["transport"] = _transport(routes)
        return original(*a, **kw)

    with patch("superseded.server.github.httpx.AsyncClient", side_effect=_client):
        patch_text, status = await app.compare_diff("tok", "o", "r", "a", "b")
    assert status == "ahead"
    assert patch_text == "PATCH"


@pytest.mark.asyncio
async def test_compare_diff_identical_returns_none():
    app = _app()
    routes = [(
        lambda req: req.url.path == "/repos/o/r/compare/a...b",
        httpx.Response(200, json={"status": "identical"}),
    )]
    original = httpx.AsyncClient

    def _client(*a, **kw):
        kw["transport"] = _transport(routes)
        return original(*a, **kw)

    with patch("superseded.server.github.httpx.AsyncClient", side_effect=_client):
        patch_text, status = await app.compare_diff("tok", "o", "r", "a", "b")
    assert status == "identical"
    assert patch_text is None


@pytest.mark.asyncio
async def test_compare_diff_behind_normalized_to_diverged():
    app = _app()
    routes = [(
        lambda req: req.url.path == "/repos/o/r/compare/a...b",
        httpx.Response(200, json={"status": "behind"}),
    )]
    original = httpx.AsyncClient

    def _client(*a, **kw):
        kw["transport"] = _transport(routes)
        return original(*a, **kw)

    with patch("superseded.server.github.httpx.AsyncClient", side_effect=_client):
        patch_text, status = await app.compare_diff("tok", "o", "r", "a", "b")
    assert status == "diverged"
    assert patch_text is None


@pytest.mark.asyncio
async def test_compare_diff_http_error_raises():
    app = _app()
    routes = [(
        lambda req: req.url.path == "/repos/o/r/compare/a...b",
        httpx.Response(500),
    )]
    original = httpx.AsyncClient

    def _client(*a, **kw):
        kw["transport"] = _transport(routes)
        return original(*a, **kw)

    with (
        patch("superseded.server.github.httpx.AsyncClient", side_effect=_client),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await app.compare_diff("tok", "o", "r", "a", "b")
```

Add `from unittest.mock import patch` to imports if not present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server_github.py -k compare_diff -v`
Expected: FAIL with `AttributeError: 'GitHubApp' object has no attribute 'compare_diff'`.

- [ ] **Step 3: Implement the method**

Add to `GitHubApp` in `src/superseded/server/github.py` (after `fetch_pr_description`):

```python
    async def compare_diff(
        self, token: str, owner: str, repo: str, base: str, head: str
    ) -> tuple[str | None, str]:
        """Fetch the incremental diff between two commits via the compare API.

        Returns ``(patch_or_none, status)`` with ``status`` ∈ ``{"ahead",
        "identical", "diverged"}``. ``"behind"`` is normalized to
        ``"diverged"``. Raises ``httpx.HTTPStatusError`` on a non-2xx response;
        callers fall back to a full review.
        """
        url = f"https://api.github.com/repos/{owner}/{repo}/compare/{base}...{head}"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self._api_headers(token))
            response.raise_for_status()
            status = response.json().get("status", "diverged")
            if status == "behind":
                status = "diverged"
            if status != "ahead":
                return None, status
            diff_response = await client.get(
                url,
                headers=self._api_headers(token, accept="application/vnd.github.v3.diff"),
            )
            diff_response.raise_for_status()
            return diff_response.text, "ahead"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server_github.py -v`
Expected: PASS (existing + 4 new).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check src/superseded/server/github.py tests/test_server_github.py
uv run ruff format src/superseded/server/github.py tests/test_server_github.py
git add src/superseded/server/github.py tests/test_server_github.py
git commit -m "feat(server): GitHubApp.compare_diff for incremental diffs"
```

---

## Task 7: Server worker progressive flow

**Files:**
- Modify: `src/superseded/server/worker.py` (`_run_review_for_job`)
- Test: `tests/test_server_worker.py`

The worker already has `job.head_sha` and a `store`. Insert the progressive resolution between `_load_safe_config` and the existing `fetch_pr_diff` call. Also handle the no-op case by short-circuiting with a success `ReviewOutcome`.

- [ ] **Step 1: Write the failing tests**

The existing `test_run_review_for_job_passes_context` mocks `superseded.server.worker._load_safe_config`? Check first — it mocks `superseded.config.load_config`. Look at the actual test file: it patches `superseded.server.worker.checkout_repo`, `superseded.config.load_config`, etc. But `_run_review_for_job` calls `_load_safe_config` (which calls `github.fetch_repo_file` + builds `Config(**data)`). For progressive tests, provide a `FakeGitHubApp` whose `fetch_repo_file` returns `None` (so `_load_safe_config` falls back to `Config()` defaults, which has `progressive=True`).

Append to `tests/test_server_worker.py`. Add `compare_diff` to `FakeGitHubApp` first:

```python
@dataclass
class FakeGitHubApp:
    get_installation_token: AsyncMock = field(
        default_factory=lambda: AsyncMock(return_value="ghp_fake")
    )
    fetch_pr_diff: AsyncMock = field(
        default_factory=lambda: AsyncMock(return_value="diff --git a/x.py")
    )
    fetch_pr_description: AsyncMock = field(
        default_factory=lambda: AsyncMock(return_value="PR desc")
    )
    fetch_repo_file: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=None))
    post_review: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=[1, 2]))
    create_check_run: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=42))
    update_check_run: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=42))
    compare_diff: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=(None, "identical")))
```

(If `FakeGitHubApp` already exists in the file, add only the `compare_diff` line to it.)

Now a shared `_job()` helper and the progressive tests:

```python
def _progressive_job(head_sha: str = "abc123") -> ReviewJob:
    return ReviewJob(
        installation_id=123,
        owner="octocat",
        repo="hello-world",
        pr_number=42,
        head_sha=head_sha,
        base_sha="def456",
    )


@pytest.mark.asyncio
async def test_worker_progressive_incremental_skips_full_diff(tmp_path):
    from superseded.memory.store import MemoryStore
    from superseded.server.worker import _run_review_for_job

    github = FakeGitHubApp()
    github.compare_diff = AsyncMock(return_value=("INCREMENTAL", "ahead"))
    repo_manager = FakeRepoManager()
    store = MemoryStore(db_path=tmp_path / "s.db")
    await store.init()
    await store.set_watermark("octocat/hello-world", 42, "oldbase")
    job = _progressive_job(head_sha="newhead")

    mock_engine = MagicMock()
    mock_engine.review.return_value = MagicMock(findings=[], summary={})

    with (
        patch("superseded.server.worker.checkout_repo", new_callable=AsyncMock) as mock_checkout,
        patch("superseded.review.engine.ReviewEngine.select", return_value=mock_engine),
        patch("superseded.context.gathering.compute_file_context", return_value="ctx"),
        patch("superseded.server.worker.build_review_payload", return_value={"body": "", "comments": [], "event": "COMMENT"}),
    ):
        mock_checkout.return_value = tmp_path
        outcome = await _run_review_for_job(
            github=github, repo_manager=repo_manager, token="tok",
            job=job, correlation_id="c", store=store,
        )

    github.compare_diff.assert_awaited_once_with("tok", "octocat", "hello-world", "oldbase", "newhead")
    github.fetch_pr_diff.assert_not_awaited()
    assert await store.get_watermark("octocat/hello-world", 42) == "newhead"


@pytest.mark.asyncio
async def test_worker_progressive_noop_returns_success_without_review(tmp_path):
    from superseded.memory.store import MemoryStore
    from superseded.server.worker import _run_review_for_job

    github = FakeGitHubApp()
    github.compare_diff = AsyncMock(return_value=(None, "identical"))
    repo_manager = FakeRepoManager()
    store = MemoryStore(db_path=tmp_path / "s.db")
    await store.init()
    await store.set_watermark("octocat/hello-world", 42, "samehead")
    job = _progressive_job(head_sha="samehead")

    with (
        patch("superseded.server.worker.checkout_repo", new_callable=AsyncMock) as mock_checkout,
    ):
        mock_checkout.return_value = tmp_path
        outcome = await _run_review_for_job(
            github=github, repo_manager=repo_manager, token="tok",
            job=job, correlation_id="c", store=store,
        )

    assert outcome.conclusion == "success"
    assert "No new commits" in outcome.title
    github.fetch_pr_diff.assert_not_awaited()
    github.post_review.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_progressive_diverged_falls_back_to_full(tmp_path):
    from superseded.memory.store import MemoryStore
    from superseded.server.worker import _run_review_for_job

    github = FakeGitHubApp()
    github.compare_diff = AsyncMock(return_value=(None, "diverged"))
    github.fetch_pr_diff = AsyncMock(return_value="FULLDIFF")
    repo_manager = FakeRepoManager()
    store = MemoryStore(db_path=tmp_path / "s.db")
    await store.init()
    await store.set_watermark("octocat/hello-world", 42, "oldbase")
    job = _progressive_job(head_sha="newhead")

    mock_engine = MagicMock()
    mock_engine.review.return_value = MagicMock(findings=[], summary={})

    with (
        patch("superseded.server.worker.checkout_repo", new_callable=AsyncMock) as mock_checkout,
        patch("superseded.review.engine.ReviewEngine.select", return_value=mock_engine),
        patch("superseded.context.gathering.compute_file_context", return_value="ctx"),
        patch("superseded.server.worker.build_review_payload", return_value={"body": "", "comments": [], "event": "COMMENT"}),
    ):
        mock_checkout.return_value = tmp_path
        await _run_review_for_job(
            github=github, repo_manager=repo_manager, token="tok",
            job=job, correlation_id="c", store=store,
        )

    github.fetch_pr_diff.assert_awaited_once()
    assert await store.get_watermark("octocat/hello-world", 42) == "newhead"


@pytest.mark.asyncio
async def test_worker_progressive_disabled_uses_full_diff(tmp_path):
    from superseded.memory.store import MemoryStore
    from superseded.server.worker import _run_review_for_job

    github = FakeGitHubApp()
    github.compare_diff = AsyncMock(return_value=("SHOULD_NOT_BE_USED", "ahead"))
    repo_manager = FakeRepoManager()
    store = MemoryStore(db_path=tmp_path / "s.db")
    await store.init()
    await store.set_watermark("octocat/hello-world", 42, "oldbase")
    job = _progressive_job(head_sha="newhead")

    mock_engine = MagicMock()
    mock_engine.review.return_value = MagicMock(findings=[], summary={})

    # Force progressive off by making _load_safe_config return Config(progressive=False).
    fake_config_yaml = "progressive: false\nagent: claude-code\n"
    github.fetch_repo_file = AsyncMock(return_value=fake_config_yaml)

    with (
        patch("superseded.server.worker.checkout_repo", new_callable=AsyncMock) as mock_checkout,
        patch("superseded.review.engine.ReviewEngine.select", return_value=mock_engine),
        patch("superseded.context.gathering.compute_file_context", return_value="ctx"),
        patch("superseded.server.worker.build_review_payload", return_value={"body": "", "comments": [], "event": "COMMENT"}),
    ):
        mock_checkout.return_value = tmp_path
        await _run_review_for_job(
            github=github, repo_manager=repo_manager, token="tok",
            job=job, correlation_id="c", store=store,
        )

    github.compare_diff.assert_not_awaited()
    github.fetch_pr_diff.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_progressive_no_store_uses_full_diff(tmp_path):
    from superseded.server.worker import _run_review_for_job

    github = FakeGitHubApp()
    github.compare_diff = AsyncMock(return_value=("SHOULD_NOT_BE_USED", "ahead"))
    repo_manager = FakeRepoManager()
    job = _progressive_job(head_sha="newhead")

    mock_engine = MagicMock()
    mock_engine.review.return_value = MagicMock(findings=[], summary={})

    with (
        patch("superseded.server.worker.checkout_repo", new_callable=AsyncMock) as mock_checkout,
        patch("superseded.review.engine.ReviewEngine.select", return_value=mock_engine),
        patch("superseded.context.gathering.compute_file_context", return_value="ctx"),
        patch("superseded.server.worker.build_review_payload", return_value={"body": "", "comments": [], "event": "COMMENT"}),
    ):
        mock_checkout.return_value = tmp_path
        await _run_review_for_job(
            github=github, repo_manager=repo_manager, token="tok",
            job=job, correlation_id="c", store=None,
        )

    github.compare_diff.assert_not_awaited()
    github.fetch_pr_diff.assert_awaited_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server_worker.py -k progressive -v`
Expected: FAIL — worker doesn't yet call `compare_diff` or short-circuit on no-op.

- [ ] **Step 3: Implement the progressive flow in `_run_review_for_job`**

In `src/superseded/server/worker.py`, replace the diff-fetch block:

```python
        diff = await github.fetch_pr_diff(token, job.owner, job.repo, job.pr_number)
        if len(diff) > MAX_DIFF_CHARS:
            diff = diff[:MAX_DIFF_CHARS] + (f"\n\n... (diff truncated at {MAX_DIFF_CHARS:,} chars)")
```

with a progressive-aware version:

```python
        repo_key = f"{job.owner}/{job.repo}"
        incremental: str | None = None
        if config.progressive and store is not None:
            watermark = await store.get_watermark(repo_key, job.pr_number)
            if watermark is not None:
                if watermark == job.head_sha:
                    logger.info(
                        "review_skipped_noop",
                        extra={"correlation_id": correlation_id, "repo": repo_key, "pr": job.pr_number},
                    )
                    return ReviewOutcome(
                        conclusion="success",
                        title="No new commits since last review",
                        summary=f"Head {job.head_sha[:7]} unchanged since last review.",
                    )
                try:
                    patch, status = await github.compare_diff(
                        token, job.owner, job.repo, watermark, job.head_sha
                    )
                except Exception:
                    logger.warning(
                        "compare_failed",
                        extra={"correlation_id": correlation_id, "repo": repo_key, "pr": job.pr_number},
                    )
                    patch, status = None, "diverged"
                if status == "ahead" and patch is not None:
                    incremental = patch
                    logger.info(
                        "review_progressive",
                        extra={"correlation_id": correlation_id, "mode": "incremental",
                               "base_sha": watermark, "head_sha": job.head_sha},
                    )

        if incremental is not None:
            diff = incremental
        else:
            diff = await github.fetch_pr_diff(token, job.owner, job.repo, job.pr_number)
            logger.info(
                "review_progressive",
                extra={"correlation_id": correlation_id, "mode": "full",
                       "head_sha": job.head_sha},
            )
        if len(diff) > MAX_DIFF_CHARS:
            diff = diff[:MAX_DIFF_CHARS] + (f"\n\n... (diff truncated at {MAX_DIFF_CHARS:,} chars)")
```

Then add the watermark write at the end of the success path. Locate the existing block:

```python
        if store is not None:
            repo_key = f"{job.owner}/{job.repo}"
            async with store:
                for f in result.findings:
                    await store.record_finding(...)
                for finding, cid in zip(result.findings, comment_ids, strict=True):
                    if cid is not None:
                        await store.set_comment_id(finding.id, cid)
```

Append the watermark set inside the same `async with store:` block (so it shares the connection):

```python
                await store.set_watermark(repo_key, job.pr_number, job.head_sha)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server_worker.py -v`
Expected: PASS (all existing + 5 new). If an existing worker test now fails because it didn't expect `compare_diff` to be called, that test either passes `store=None` (so progressive is skipped) or should be updated to pass `store=None` explicitly. Check each failure and pass `store=None` where the test predates progressive.

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check src/superseded/server/worker.py tests/test_server_worker.py
uv run ruff format src/superseded/server/worker.py tests/test_server_worker.py
git add src/superseded/server/worker.py tests/test_server_worker.py
git commit -m "feat(server): progressive review flow in worker"
```

---

## Task 8: Final verification

- [ ] **Step 1: Full lint**

Run: `uv run ruff check src/ tests/`
Expected: no errors.

- [ ] **Step 2: Full format check**

Run: `uv run ruff format --check src/ tests/`
Expected: no differences. If differences reported, run `uv run ruff format src/ tests/` and re-stage.

- [ ] **Step 3: Full test suite**

Run: `uv run pytest tests/ -v`
Expected: all PASS. Pay attention to any pre-existing test that mocked `fetch_diff` for `--pr` and now needs `_resolve_pr_review_diff` mocked or `--no-memory` added (per Task 5 Step B5 guidance).

- [ ] **Step 4: Smoke test the CLI locally (manual)**

```bash
uv run superseded review --pr <some-open-pr> --format table --no-memory   # full review (memory off)
uv run superseded review --pr <some-open-pr> --format table                # first run -> full, writes watermark
uv run superseded review --pr <same-pr> --format table                     # second run -> "No new commits" OR incremental
uv run superseded review --pr <same-pr> --full                             # forces full, advances watermark
```

(Only run this if `gh` is authenticated and there's a real PR; otherwise skip — the automated tests cover the logic.)

---

## Self-Review Checklist (run after writing, before handoff)

**Spec coverage:**
- Watermark table + get/set + migration → Task 2 ✓
- `fetch_pr_head_sha` → Task 3 ✓
- `fetch_incremental_diff` + `IncrementalDiffError` + status map (ahead/identical/diverged/behind→diverged) + argv shape → Task 4 ✓
- `--full` flag, on-by-default, `--no-memory`/disabled paths, watermark-only-after-success, noop empty result, fallback on stale/error → Task 5 ✓
- `config.progressive` field → Task 1 ✓
- Server `compare_diff` (httpx) → Task 6 ✓
- Server worker progressive flow: incremental / noop / diverged-fallback / `config.progressive=false` / `store=None` → Task 7 ✓
- Edge case "engine failure → no watermark advance" → Task 5 Step B1 (`test_review_engine_failure_does_not_advance_watermark`) ✓
- Edge case "PR number reuse self-heals" → covered by `diverged` path (Tasks 4 & 7) ✓

**Placeholder scan:** none (every step has concrete code/commands).

**Type/signature consistency:**
- `_resolve_pr_review_diff` returns `(str | None, str, str)` everywhere (Task 5 helper + tests) ✓
- `compare_diff` returns `(str | None, str)` everywhere (Task 6 impl + Task 7 usage + tests) ✓
- `set_watermark(repo, pr_number, head_sha)` / `get_watermark(repo, pr_number)` consistent across Tasks 2, 5, 7 ✓
- `head_sha` threading in `_run_review`: returned from helper, used for watermark write, `None` on non-PR/non-progressive paths ✓

---

## Notes / Errata

- **Spec correction:** the design doc (Section 2 / Section 4) mentions the server using `aiohttp` for `compare_diff`. The actual server (`src/superseded/server/github.py`) uses **httpx**. This plan uses httpx (Task 6), which matches the existing code. No change to the spec is required for implementation to proceed, but the doc's `aiohttp` references are superseded by this plan.
- **`fmt` scoping in `_run_review`:** Task 5 Step B4 moves the `fmt = output_format or config.format` assignment earlier in the function. Ensure it isn't also left in its old location (would cause a redundant assignment — not an error, but ruff may flag it).
