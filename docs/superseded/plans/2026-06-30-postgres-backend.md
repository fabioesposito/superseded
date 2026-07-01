# Postgres Backend for Server Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let operators point `superseded serve` at Postgres via a single `database_url` config field, while leaving the local CLI path on SQLite unchanged.

**Architecture:** Introduce a `Store` `typing.Protocol` describing the surface the server consumes; keep the existing `MemoryStore` (SQLite) structurally conforming; add a new `PostgresStore` backed by an `asyncpg` connection pool; select between them with a `make_store(database_url)` factory. Move the two stats SQL queries currently living in `StatsAggregator` into store methods so the same surface works for both backends.

**Tech Stack:** Python 3.14, pydantic v2, aiosqlite (existing), asyncpg (new), FastAPI/uvicorn (server), pytest + pytest-asyncio.

**Spec:** `docs/superseded/specs/2026-06-30-postgres-backend-design.md`

**Conventions (from AGENTS.md):**
- Every module starts with `from __future__ import annotations`.
- Run lint/format/tests via uv: `uv run ruff check src/ tests/`, `uv run ruff format src/ tests/`, `uv run pytest tests/ -v`.
- Ruff rules `E,W,F,I,N,UP,B,SIM,TCH,RUF` with `E501,B008,TC001-003,E741` ignored. Line length 100, double quotes.
- `asyncio_mode = "auto"` — async test functions need no marker.
- Never edit `uv.lock` by hand; change deps in `pyproject.toml` then `uv lock`/`uv sync`.

---

## File Structure

**Create:**
- `src/superseded/memory/backend.py` — `Store` Protocol + `make_store()` factory.
- `src/superseded/memory/postgres.py` — `PostgresStore` class, its `SCHEMA`, all SQL.
- `tests/test_memory_backend.py` — unit tests for `make_store` dispatch (no live DB).
- `tests/test_postgres_store.py` — behavioral tests, `@pytest.mark.postgres`, skipped unless `SUPERSEDED_POSTGRES_TEST_DSN` set.

**Modify:**
- `pyproject.toml` — add `asyncpg` dep; register `postgres` marker; default `addopts` excludes it.
- `src/superseded/memory/store.py` — add two methods (`get_review_stats`, `refresh_review_stats`) by moving SQL out of `StatsAggregator`. No other change.
- `src/superseded/audit/stats.py` — remove `_db()` access; call the new store methods; drop `_CASE_EXPR` / `_classify_file_pattern` (now inlined into store SQL).
- `src/superseded/server/config.py` — add `database_url: str | None = None`; read from env + YAML.
- `src/superseded/server/worker.py`, `src/superseded/server/app.py` — widen `MemoryStore` type hints to `Store` under `TYPE_CHECKING`.
- `src/superseded/cli.py` — `serve` builds the store via `make_store(config.database_url, max_size=...)`; lifespan closes it.
- `tests/test_audit_stats.py` — replace `store._db()` assertions with store-method calls; add coverage for the new `MemoryStore` stats methods.

**Unchanged:** `tests/test_memory_store.py`, `tests/test_memory.py`, `tests/test_integration.py`, `tests/test_server_*.py` (they construct `MemoryStore` directly and exercise the unchanged SQLite path).

---

## Task 1: Add asyncpg dependency and pytest marker

**Files:**
- Modify: `pyproject.toml`

- [x] **Step 1: Add asyncpg and pytest config**

Replace the `[project]` dependencies block and the `[tool.pytest.ini_options]` block in `pyproject.toml`:

```toml
[project]
name = "superseded"
version = "0.1.0"
description = "Reviews that supersede themselves."
requires-python = ">=3.14"
dependencies = [
    "click>=8.1.0",
    "pydantic>=2.10.0",
    "pyyaml>=6.0.0",
    "aiosqlite>=0.21.0",
    "asyncpg>=0.30.0",
    "fastapi>=0.115.0",
    "uvicorn>=0.34.0",
]
```

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
    "postgres: requires a live Postgres (set SUPERSEDED_POSTGRES_TEST_DSN)",
]
addopts = "-m 'not postgres'"
```

- [x] **Step 2: Sync the lockfile and venv**

Run: `uv sync`
Expected: `asyncpg` resolves and installs; `Resolved X packages` printed with no error.

- [x] **Step 3: Verify the default test run still excludes the marker (no marker tests exist yet, so just confirm green)**

Run: `uv run pytest tests/ -q`
Expected: existing suite passes (no regressions from dep change).

- [x] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: add asyncpg; register postgres pytest marker"
```

---

## Task 2: Move stats SQL from StatsAggregator into MemoryStore

The `StatsAggregator` reaches into `store._db()` and sets `aiosqlite.Row` directly, which breaks for any non-SQLite store. Move the two SQL queries into `MemoryStore` methods that the (soon-to-exist) `Store` Protocol will require.

**Files:**
- Modify: `src/superseded/memory/store.py`
- Modify: `src/superseded/audit/stats.py`
- Modify: `tests/test_audit_stats.py`

- [x] **Step 1: Write failing tests for the new MemoryStore stats methods**

Add to the **end** of `tests/test_audit_stats.py` (keep all existing imports; the file already imports `aiosqlite`, `MemoryStore`, `Path`):

```python
async def test_memory_store_refresh_review_stats(tmp_path):
    from superseded.memory.store import MemoryStore

    store = MemoryStore(Path(tmp_path) / "st.db")
    await store.open()
    try:
        await store.record_finding("f1", "octo/r", "security", "critical", "a.py", 1, "t", "d")
        await store.record_finding("f2", "octo/r", "security", "critical", "tests/b.py", 2, "t", "d")
        await store.record_feedback("f1", "helpful")
        await store.record_feedback("f2", "dismiss")
        await store.refresh_review_stats("octo/r")
        rows = await store.get_review_stats("octo/r", min_sample=1)
        assert len(rows) == 2  # two file_patterns: '*' and 'test'
        by_pat = {r["file_pattern"]: r for r in rows}
        star = by_pat["*"]
        assert star["total"] == 1 and star["accepted"] == 1 and star["dismissed"] == 0
        test_pat = by_pat["test"]
        assert test_pat["total"] == 1 and test_pat["dismissed"] == 1 and test_pat["accepted"] == 0
    finally:
        await store.close()


async def test_memory_store_get_review_stats_respects_min_sample(tmp_path):
    from superseded.memory.store import MemoryStore

    store = MemoryStore(Path(tmp_path) / "st.db")
    await store.open()
    try:
        await store.record_finding("f1", "octo/r", "security", "critical", "a.py", 1, "t", "d")
        await store.record_feedback("f1", "helpful")
        await store.refresh_review_stats("octo/r")
        # total=1 < min_sample=5 → filtered out
        assert await store.get_review_stats("octo/r", min_sample=5) == []
        assert len(await store.get_review_stats("octo/r", min_sample=1)) == 1
    finally:
        await store.close()
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_audit_stats.py::test_memory_store_refresh_review_stats tests/test_audit_stats.py::test_memory_store_get_review_stats_respects_min_sample -v`
Expected: FAIL with `AttributeError: 'MemoryStore' object has no attribute 'refresh_review_stats'` (or `get_review_stats`).

- [x] **Step 3: Add the two methods to MemoryStore**

In `src/superseded/memory/store.py`, add a module-level constant just below the existing `SCHEMA` string (copy the CASE expression verbatim from `audit/stats.py`):

```python
_STATS_FILE_PATTERN_CASE = """\
CASE
    WHEN f.file LIKE 'test/%' OR f.file LIKE 'tests/%'
         OR f.file LIKE '%%_test.%%' OR f.file LIKE 'test_%%'
         OR f.file LIKE '%%__test__/%%' THEN 'test'
    WHEN f.file LIKE '%%migrations/%%' THEN 'migration'
    WHEN f.file LIKE '%%.yaml' OR f.file LIKE '%%.yml'
         OR f.file LIKE '%%.toml' OR f.file LIKE '%%.json'
         OR f.file LIKE 'Dockerfile%%' THEN 'config'
    ELSE '*'
END"""
```

Add these two methods as the last methods of the `MemoryStore` class (after `set_reflection_state`):

```python
    async def refresh_review_stats(self, repo: str) -> None:
        async with self._db() as db:
            await db.execute(
                f"INSERT INTO review_stats "
                f"(repo, pass, severity, file_pattern, total, accepted, dismissed) "
                f"SELECT f.repo, f.pass, f.severity, "
                f"{_STATS_FILE_PATTERN_CASE} AS file_pattern, "
                f"COUNT(*) AS total, "
                f"COUNT(*) FILTER (WHERE fb.action = 'helpful') AS accepted, "
                f"COUNT(*) FILTER (WHERE fb.action = 'dismiss') AS dismissed "
                f"FROM findings f "
                f"JOIN feedback fb ON fb.finding_id = f.id "
                f"WHERE f.repo = ? "
                f"GROUP BY f.repo, f.pass, f.severity, file_pattern "
                f"ON CONFLICT(repo, pass, severity, file_pattern) DO UPDATE SET "
                f"total = excluded.total, "
                f"accepted = excluded.accepted, "
                f"dismissed = excluded.dismissed, "
                f"updated_at = CURRENT_TIMESTAMP",
                (repo,),
            )
            await db.commit()

    async def get_review_stats(self, repo: str, min_sample: int) -> list[dict]:
        async with self._db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM review_stats WHERE repo = ? AND total >= ?",
                (repo, min_sample),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
```

- [x] **Step 4: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_audit_stats.py::test_memory_store_refresh_review_stats tests/test_audit_stats.py::test_memory_store_get_review_stats_respects_min_sample -v`
Expected: PASS (both).

- [x] **Step 5: Refactor StatsAggregator to call the new store methods**

Replace the entire body of `src/superseded/audit/stats.py` with:

```python
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from superseded.memory.backend import Store


class StatsAggregator:
    MIN_SAMPLE = 5

    def __init__(self, store: Store) -> None:
        self._store = store

    async def get_stats_context(self, repo: str) -> str | None:
        """Query review_stats for repo, format as guidance text.

        Returns None if no rows meet the MIN_SAMPLE threshold.
        """
        rows = await self._store.get_review_stats(repo, self.MIN_SAMPLE)

        if not rows:
            return None

        hints: list[str] = []
        for r in rows:
            total = r["total"]
            dismiss_rate = r["dismissed"] / total
            accept_rate = r["accepted"] / total
            fp = r["file_pattern"]
            ps = r["pass"]
            sev = r["severity"]

            if dismiss_rate > 0.8 and fp != "*":
                hints.append(
                    f"Suppress {ps}/{sev} findings on {fp} files "
                    f"(dismissal rate {dismiss_rate:.0%})."
                )
            elif dismiss_rate > 0.5:
                hints.append(
                    f"Prefer higher-severity {ps} findings (dismissal rate {dismiss_rate:.0%})."
                )
            elif accept_rate > 0.8:
                hints.append(
                    f"Continue current approach for {ps}/{sev} (acceptance rate {accept_rate:.0%})."
                )

        return "\n".join(hints) if hints else None

    async def _refresh(self, repo: str) -> None:
        """Recompute review_stats from findings+feedback for this repo."""
        await self._store.refresh_review_stats(repo)
```

Note: the `Store` import is under `TYPE_CHECKING` because the Protocol does not exist yet — this forward reference will resolve once Task 3 creates `memory/backend.py`. To keep ruff happy *now*, the file uses a string-typed forward annotation only.

- [x] **Step 6: Update existing test_audit_stats.py assertions that used _db()**

The existing tests in `test_audit_stats.py` reach into `store._db()` and set `db.row_factory = aiosqlite.Row`. They currently pass because `MemoryStore` still has `_db()`. They keep passing unchanged. **Do not modify them in this step** — Task 7 may revisit if desired, but they remain valid as-is. Verify they still pass:

Run: `uv run pytest tests/test_audit_stats.py -v`
Expected: PASS (all, including the two new tests).

- [x] **Step 7: Lint and format**

Run: `uv run ruff check src/superseded/memory/store.py src/superseded/audit/stats.py tests/test_audit_stats.py && uv run ruff format src/superseded/memory/store.py src/superseded/audit/stats.py tests/test_audit_stats.py`
Expected: no errors; any reformatting applied.

- [x] **Step 8: Run the full default suite**

Run: `uv run pytest tests/ -q`
Expected: PASS (no regressions).

- [x] **Step 9: Commit**

```bash
git add src/superseded/memory/store.py src/superseded/audit/stats.py tests/test_audit_stats.py
git commit -m "refactor: move review_stats SQL into MemoryStore methods"
```

---

## Task 3: Add the Store Protocol and make_store factory

**Files:**
- Create: `src/superseded/memory/backend.py`
- Create: `tests/test_memory_backend.py`

- [x] **Step 1: Write failing tests for make_store dispatch**

Create `tests/test_memory_backend.py`:

```python
from __future__ import annotations

from unittest.mock import patch

import pytest

from superseded.memory.backend import make_store
from superseded.memory.store import DEFAULT_DB_PATH, MemoryStore


def test_make_store_none_returns_memory_store_with_default_path():
    store = make_store(None)
    assert isinstance(store, MemoryStore)
    assert store.db_path == DEFAULT_DB_PATH


def test_make_store_empty_returns_memory_store():
    assert isinstance(make_store(""), MemoryStore)


def test_make_store_sqlite_scheme_uses_path():
    store = make_store("sqlite:///tmp/custom.db")
    assert isinstance(store, MemoryStore)
    assert str(store.db_path) == "/tmp/custom.db"


def test_make_store_sqlite_no_path_uses_default():
    store = make_store("sqlite://")
    assert isinstance(store, MemoryStore)
    assert store.db_path == DEFAULT_DB_PATH


def test_make_store_postgres_returns_postgres_store():
    from superseded.memory.postgres import PostgresStore

    with patch("superseded.memory.backend.PostgresStore") as mock_cls:
        mock_cls.return_value = object()  # don't actually connect
        store = make_store("postgres://u:p@host/db", max_size=5)
        mock_cls.assert_called_once_with("postgres://u:p@host/db", max_size=5)
        assert store is mock_cls.return_value


def test_make_store_postgresql_scheme_also_works():
    from superseded.memory.postgres import PostgresStore

    with patch("superseded.memory.backend.PostgresStore") as mock_cls:
        mock_cls.return_value = object()
        make_store("postgresql://u:p@host/db")
        mock_cls.assert_called_once()


def test_make_store_unsupported_scheme_raises():
    with pytest.raises(ValueError, match="Unsupported database scheme"):
        make_store("mysql://u:p@host/db")
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_memory_backend.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'superseded.memory.backend'`.

- [x] **Step 3: Create memory/backend.py**

Create `src/superseded/memory/backend.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from urllib.parse import urlparse

from superseded.memory.store import DEFAULT_DB_PATH, MemoryStore

if TYPE_CHECKING:
    from superseded.memory.postgres import PostgresStore


@runtime_checkable
class Store(Protocol):
    """The persistence surface consumed by the server path.

    `MemoryStore` (SQLite) and `PostgresStore` both satisfy this structurally.
    The local CLI path uses `MemoryStore` directly and does not depend on this
    Protocol at runtime.
    """

    async def open(self) -> None: ...
    async def close(self) -> None: ...
    async def init(self) -> None: ...

    async def record_finding(
        self,
        finding_id: str,
        repo: str,
        pass_name: str,
        severity: str,
        file: str,
        line: int,
        title: str,
        description: str,
        reasoning: str = "",
    ) -> None: ...

    async def set_comment_id(self, finding_id: str, comment_id: int) -> None: ...
    async def get_finding_by_comment_id(self, comment_id: int) -> dict | None: ...
    async def record_feedback(self, finding_id: str, action: str) -> None: ...
    async def record_feedback_by_comment_id(self, comment_id: int, action: str) -> bool: ...
    async def get_dismissed_findings(self, repo: str) -> list[dict]: ...

    async def record_installation(
        self, installation_id: int, owner: str, repos: list[str]
    ) -> None: ...
    async def get_installation(self, installation_id: int) -> dict | None: ...
    async def remove_installation(self, installation_id: int) -> None: ...

    async def get_watermark(self, repo: str, pr_number: int) -> str | None: ...
    async def set_watermark(self, repo: str, pr_number: int, head_sha: str) -> None: ...

    async def get_learned_rules(self, repo: str, limit: int = 5) -> list[dict]: ...
    async def get_reflection_state(self, repo: str) -> int: ...
    async def set_reflection_state(self, repo: str, last_feedback_id: int) -> None: ...

    async def refresh_review_stats(self, repo: str) -> None: ...
    async def get_review_stats(self, repo: str, min_sample: int) -> list[dict]: ...


def make_store(database_url: str | None, *, max_size: int | None = None) -> Store:
    """Return the appropriate store for a database URL.

    - `None` / empty / `sqlite://`        -> MemoryStore (default SQLite path)
    - `sqlite:///path/to.db`              -> MemoryStore at that path
    - `postgres://...` / `postgresql://...`-> PostgresStore
    - anything else                        -> ValueError
    """
    if not database_url:
        return MemoryStore()

    parsed = urlparse(database_url)
    scheme = parsed.scheme

    if scheme in ("", "sqlite"):
        if scheme == "sqlite" and parsed.path:
            return MemoryStore(db_path=Path(parsed.path))
        return MemoryStore()

    if scheme in ("postgres", "postgresql"):
        from superseded.memory.postgres import PostgresStore

        return PostgresStore(database_url, max_size=max_size)

    raise ValueError(f"Unsupported database scheme: {scheme!r}")
```

- [x] **Step 4: Run the make_store tests**

Run: `uv run pytest tests/test_memory_backend.py -v`
Expected: PASS (all 7).

- [x] **Step 5: Verify MemoryStore structurally satisfies Store**

Run a quick check via the runtime-checkable Protocol:

```bash
uv run python -c "from superseded.memory.backend import Store; from superseded.memory.store import MemoryStore; assert isinstance(MemoryStore(), Store); print('ok')"
```
Expected: prints `ok`.

Note: `runtime_checkable` only verifies attribute names exist, not signatures. The behavioral parity is covered by the per-method tests in `test_memory_store.py` + the mirrored `test_postgres_store.py` (Task 5).

- [x] **Step 6: Lint and format**

Run: `uv run ruff check src/superseded/memory/backend.py tests/test_memory_backend.py && uv run ruff format src/superseded/memory/backend.py tests/test_memory_backend.py`
Expected: no errors.

- [x] **Step 7: Run the full default suite**

Run: `uv run pytest tests/ -q`
Expected: PASS.

- [x] **Step 8: Commit**

```bash
git add src/superseded/memory/backend.py tests/test_memory_backend.py
git commit -m "feat: add Store protocol and make_store factory"
```

---

## Task 4: Implement PostgresStore

A pure-Python class implementing the `Store` Protocol against an `asyncpg` connection pool. Each method owns its SQL with `$n` placeholders.

**Files:**
- Create: `src/superseded/memory/postgres.py`
- Create: `tests/test_postgres_store.py` (skipped without env var)

- [x] **Step 1: Write the gated test module (skeleton + fixtures)**

Create `tests/test_postgres_store.py`:

```python
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.postgres

_DSN = os.environ.get("SUPERSEDED_POSTGRES_TEST_DSN")


if not _DSN:
    pytest.skip(
        "set SUPERSEDED_POSTGRES_TEST_DSN to run Postgres store tests",
        allow_module_level=True,
    )


@pytest.fixture
async def store():
    from superseded.memory.postgres import PostgresStore

    s = PostgresStore(_DSN, max_size=4)
    await s.open()
    # Wipe all tables between tests for isolation.
    async with s._pool.acquire() as conn:
        for table in (
            "feedback",
            "findings",
            "installations",
            "review_watermarks",
            "review_stats",
            "learned_rules",
            "reflection_state",
        ):
            await conn.execute(f"DELETE FROM {table}")
    yield s
    await s.close()
```

- [x] **Step 2: Add behavioral tests (mirroring test_memory_store.py)**

Append to `tests/test_postgres_store.py`:

```python
async def test_record_and_get_finding(store):
    await store.record_finding(
        "f1", "octo/r", "security", "critical", "a.py", 10, "t", "d", reasoning="r"
    )
    await store.set_comment_id("f1", 99)
    row = await store.get_finding_by_comment_id(99)
    assert row is not None
    assert row["id"] == "f1"
    assert row["severity"] == "critical"
    assert row["reasoning"] == "r"


async def test_record_finding_upsert_preserves_comment_id(store):
    await store.record_finding("f1", "octo/r", "security", "critical", "a.py", 1, "t", "d")
    await store.set_comment_id("f1", 42)
    # Re-record with new severity/description; comment_id must survive.
    await store.record_finding("f1", "octo/r", "security", "important", "a.py", 1, "t", "d2")
    row = await store.get_finding_by_comment_id(42)
    assert row is not None
    assert row["severity"] == "important"
    assert row["description"] == "d2"


async def test_feedback_round_trip(store):
    await store.record_finding("f1", "octo/r", "security", "critical", "a.py", 1, "t", "d")
    await store.set_comment_id("f1", 7)
    assert await store.record_feedback_by_comment_id(7, "dismiss") is True
    dismissed = await store.get_dismissed_findings("octo/r")
    assert len(dismissed) == 1 and dismissed[0]["id"] == "f1"


async def test_installations(store):
    await store.record_installation(123, "octo", ["repo-a"])
    inst = await store.get_installation(123)
    assert inst is not None
    assert inst["owner"] == "octo"
    assert '"repo-a"' in inst["repos"]
    await store.remove_installation(123)
    assert await store.get_installation(123) is None


async def test_watermarks(store):
    assert await store.get_watermark("octo/r", 5) is None
    await store.set_watermark("octo/r", 5, "abc")
    assert await store.get_watermark("octo/r", 5) == "abc"
    # upsert
    await store.set_watermark("octo/r", 5, "def")
    assert await store.get_watermark("octo/r", 5) == "def"


async def test_reflection_state(store):
    assert await store.get_reflection_state("octo/r") == 0
    await store.set_reflection_state("octo/r", 10)
    assert await store.get_reflection_state("octo/r") == 10


async def test_review_stats(store):
    await store.record_finding("f1", "octo/r", "security", "critical", "a.py", 1, "t", "d")
    await store.record_finding("f2", "octo/r", "security", "critical", "tests/b.py", 2, "t", "d")
    await store.record_feedback("f1", "helpful")
    await store.record_feedback("f2", "dismiss")
    await store.refresh_review_stats("octo/r")
    rows = await store.get_review_stats("octo/r", min_sample=1)
    by_pat = {r["file_pattern"]: r for r in rows}
    assert by_pat["*"]["total"] == 1 and by_pat["*"]["accepted"] == 1
    assert by_pat["test"]["total"] == 1 and by_pat["test"]["dismissed"] == 1


async def test_open_idempotent_and_close_safe(store):
    await store.open()  # second open is a no-op
    await store.close()
    await store.close()  # second close is safe
```

- [x] **Step 3: Run tests to verify they SKIP (no DSN in CI/local default)**

Run: `uv run pytest tests/test_postgres_store.py -v`
Expected: `SKIPPED` for all (file-level skip via env var absence). The default `addopts = "-m 'not postgres'"` also deselects them, so they won't even appear in a plain `uv run pytest` run.

Also verify the default suite still green:

Run: `uv run pytest tests/ -q`
Expected: PASS.

- [x] **Step 4: Implement PostgresStore**

Create `src/superseded/memory/postgres.py`:

```python
from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS findings (
    id          TEXT PRIMARY KEY,
    repo        TEXT,
    pass        TEXT,
    severity    TEXT,
    file        TEXT,
    line        INTEGER,
    reasoning   TEXT NOT NULL DEFAULT '',
    title       TEXT,
    description TEXT,
    dismissed   BOOLEAN NOT NULL DEFAULT FALSE,
    comment_id  BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS feedback (
    id          BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    finding_id  TEXT REFERENCES findings(id),
    action      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS installations (
    id                  BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    app_installation_id BIGINT UNIQUE NOT NULL,
    owner               TEXT NOT NULL,
    repos               TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS review_watermarks (
    repo        TEXT NOT NULL,
    pr_number   BIGINT NOT NULL,
    head_sha    TEXT NOT NULL,
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (repo, pr_number)
);

CREATE TABLE IF NOT EXISTS review_stats (
    repo         TEXT NOT NULL,
    pass         TEXT NOT NULL,
    severity     TEXT NOT NULL,
    file_pattern TEXT NOT NULL DEFAULT '*',
    total        BIGINT NOT NULL DEFAULT 0,
    accepted     BIGINT NOT NULL DEFAULT 0,
    dismissed    BIGINT NOT NULL DEFAULT 0,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (repo, pass, severity, file_pattern)
);

CREATE TABLE IF NOT EXISTS learned_rules (
    id              BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    repo            TEXT NOT NULL,
    rule_text       TEXT NOT NULL,
    evidence_count  BIGINT NOT NULL DEFAULT 0,
    confidence      DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_applied_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS reflection_state (
    repo               TEXT NOT NULL,
    last_feedback_id   BIGINT NOT NULL,
    last_reflection_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (repo)
);
"""

_STATS_FILE_PATTERN_CASE = """\
CASE
    WHEN f.file LIKE 'test/%' OR f.file LIKE 'tests/%'
         OR f.file LIKE '%_test.%' OR f.file LIKE 'test_%'
         OR f.file LIKE '%__test__/%' THEN 'test'
    WHEN f.file LIKE '%migrations/%' THEN 'migration'
    WHEN f.file LIKE '%.yaml' OR f.file LIKE '%.yml'
         OR f.file LIKE '%.toml' OR f.file LIKE '%.json'
         OR f.file LIKE 'Dockerfile%' THEN 'config'
    ELSE '*'
END"""


def _row_to_dict(row: asyncpg.Record | None) -> dict | None:
    return dict(row) if row is not None else None


class PostgresStore:
    """Postgres-backed implementation of the Store protocol.

    Uses a process-wide asyncpg connection pool created in `open()` and closed
    in `close()`. Every method acquires a connection from the pool. SQL uses
    Postgres-native `$n` placeholders and `ON CONFLICT` upserts.
    """

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int | None = None,
    ) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: asyncpg.Pool | None = None

    async def open(self) -> None:
        if self._pool is not None:
            return
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=self._min_size,
            max_size=self._max_size,
            command_timeout=30,
        )
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            await conn.execute(SCHEMA)
        logger.info(
            "Postgres pool opened (min=%d max=%s)", self._min_size, self._max_size
        )
        logger.info("Postgres schema ensured")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def init(self) -> None:
        await self.open()

    def _conn(self) -> asyncpg.PoolAcquireContext:
        assert self._pool is not None, "PostgresStore.open() not called"
        return self._pool.acquire()

    async def record_finding(
        self,
        finding_id: str,
        repo: str,
        pass_name: str,
        severity: str,
        file: str,
        line: int,
        title: str,
        description: str,
        reasoning: str = "",
    ) -> None:
        async with self._conn() as conn:
            await conn.execute(
                "INSERT INTO findings "
                "(id, repo, pass, severity, file, line, title, description, reasoning) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) "
                "ON CONFLICT(id) DO UPDATE SET "
                "severity = EXCLUDED.severity, "
                "description = EXCLUDED.description, "
                "reasoning = EXCLUDED.reasoning "
                "WHERE EXCLUDED.severity IS DISTINCT FROM findings.severity "
                "OR EXCLUDED.description IS DISTINCT FROM findings.description "
                "OR EXCLUDED.reasoning IS DISTINCT FROM findings.reasoning",
                finding_id, repo, pass_name, severity, file, line, title, description, reasoning,
            )

    async def set_comment_id(self, finding_id: str, comment_id: int) -> None:
        async with self._conn() as conn:
            await conn.execute(
                "UPDATE findings SET comment_id = $1 WHERE id = $2",
                comment_id, finding_id,
            )

    async def get_finding_by_comment_id(self, comment_id: int) -> dict | None:
        async with self._conn() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM findings WHERE comment_id = $1", comment_id
            )
            return _row_to_dict(row)

    async def record_feedback(self, finding_id: str, action: str) -> None:
        async with self._conn() as conn:
            await conn.execute(
                "INSERT INTO feedback (finding_id, action) VALUES ($1, $2)",
                finding_id, action,
            )
            if action == "dismiss":
                await conn.execute(
                    "UPDATE findings SET dismissed = TRUE WHERE id = $1", finding_id
                )

    async def record_feedback_by_comment_id(self, comment_id: int, action: str) -> bool:
        finding = await self.get_finding_by_comment_id(comment_id)
        if finding is None:
            return False
        await self.record_feedback(finding["id"], action)
        return True

    async def get_dismissed_findings(self, repo: str) -> list[dict]:
        async with self._conn() as conn:
            rows = await conn.fetch(
                "SELECT * FROM findings WHERE repo = $1 AND dismissed = TRUE", repo
            )
            return [dict(r) for r in rows]

    async def record_installation(
        self, installation_id: int, owner: str, repos: list[str]
    ) -> None:
        async with self._conn() as conn:
            await conn.execute(
                "INSERT INTO installations (app_installation_id, owner, repos) "
                "VALUES ($1, $2, $3) "
                "ON CONFLICT (app_installation_id) DO UPDATE SET "
                "owner = EXCLUDED.owner, repos = EXCLUDED.repos",
                installation_id, owner, json.dumps(repos),
            )

    async def get_installation(self, installation_id: int) -> dict | None:
        async with self._conn() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM installations WHERE app_installation_id = $1",
                installation_id,
            )
            return _row_to_dict(row)

    async def remove_installation(self, installation_id: int) -> None:
        async with self._conn() as conn:
            await conn.execute(
                "DELETE FROM installations WHERE app_installation_id = $1",
                installation_id,
            )

    async def get_watermark(self, repo: str, pr_number: int) -> str | None:
        async with self._conn() as conn:
            row = await conn.fetchrow(
                "SELECT head_sha FROM review_watermarks WHERE repo = $1 AND pr_number = $2",
                repo, pr_number,
            )
            return row["head_sha"] if row is not None else None

    async def set_watermark(self, repo: str, pr_number: int, head_sha: str) -> None:
        async with self._conn() as conn:
            await conn.execute(
                "INSERT INTO review_watermarks (repo, pr_number, head_sha) "
                "VALUES ($1, $2, $3) "
                "ON CONFLICT (repo, pr_number) DO UPDATE SET head_sha = EXCLUDED.head_sha",
                repo, pr_number, head_sha,
            )

    async def get_learned_rules(self, repo: str, limit: int = 5) -> list[dict]:
        async with self._conn() as conn:
            rows = await conn.fetch(
                "SELECT * FROM learned_rules "
                "WHERE repo = $1 AND confidence >= 0.3 "
                "ORDER BY confidence DESC, created_at DESC "
                "LIMIT $2",
                repo, limit,
            )
            rules = [dict(r) for r in rows]
            if rules:
                ids: list[int] = [r["id"] for r in rules]
                # Build $n placeholders for the IN list (starts at $1: no other params here).
                placeholders = ",".join(f"${i + 1}" for i in range(len(ids)))
                await conn.execute(
                    f"UPDATE learned_rules SET last_applied_at = NOW() "
                    f"WHERE id IN ({placeholders})",
                    *ids,
                )
            return rules

    async def get_reflection_state(self, repo: str) -> int:
        async with self._conn() as conn:
            row = await conn.fetchrow(
                "SELECT last_feedback_id FROM reflection_state WHERE repo = $1", repo
            )
            return row["last_feedback_id"] if row is not None else 0

    async def set_reflection_state(self, repo: str, last_feedback_id: int) -> None:
        async with self._conn() as conn:
            await conn.execute(
                "INSERT INTO reflection_state (repo, last_feedback_id) "
                "VALUES ($1, $2) "
                "ON CONFLICT (repo) DO UPDATE SET last_feedback_id = EXCLUDED.last_feedback_id",
                repo, last_feedback_id,
            )

    async def refresh_review_stats(self, repo: str) -> None:
        async with self._conn() as conn:
            await conn.execute(
                f"INSERT INTO review_stats "
                f"(repo, pass, severity, file_pattern, total, accepted, dismissed) "
                f"SELECT f.repo, f.pass, f.severity, "
                f"{_STATS_FILE_PATTERN_CASE} AS file_pattern, "
                f"COUNT(*) AS total, "
                f"COUNT(*) FILTER (WHERE fb.action = 'helpful') AS accepted, "
                f"COUNT(*) FILTER (WHERE fb.action = 'dismiss') AS dismissed "
                f"FROM findings f "
                f"JOIN feedback fb ON fb.finding_id = f.id "
                f"WHERE f.repo = $1 "
                f"GROUP BY f.repo, f.pass, f.severity, file_pattern "
                f"ON CONFLICT (repo, pass, severity, file_pattern) DO UPDATE SET "
                f"total = EXCLUDED.total, "
                f"accepted = EXCLUDED.accepted, "
                f"dismissed = EXCLUDED.dismissed, "
                f"updated_at = NOW()",
                repo,
            )

    async def get_review_stats(self, repo: str, min_sample: int) -> list[dict]:
        async with self._conn() as conn:
            rows = await conn.fetch(
                "SELECT * FROM review_stats WHERE repo = $1 AND total >= $2",
                repo, min_sample,
            )
            return [dict(r) for r in rows]
```

- [x] **Step 5: Verify the test module at least imports cleanly (still skipping)**

Run: `uv run pytest tests/test_postgres_store.py -v -m postgres --no-header`
Expected: module-level skip kicks in (no DSN) → `SKIPPED`. No import errors.

- [x] **Step 6: Verify PostgresStore structurally satisfies Store**

```bash
uv run python -c "from superseded.memory.backend import Store; from superseded.memory.postgres import PostgresStore; assert isinstance(PostgresStore('postgres://x'), Store); print('ok')"
```
Expected: prints `ok`. (`PostgresStore('postgres://x')` does not connect; it only stores the DSN string.)

- [x] **Step 7: Lint and format**

Run: `uv run ruff check src/superseded/memory/postgres.py tests/test_postgres_store.py && uv run ruff format src/superseded/memory/postgres.py tests/test_postgres_store.py`
Expected: no errors.

- [x] **Step 8: Run the full default suite (must remain green, Postgres tests skipped)**

Run: `uv run pytest tests/ -q`
Expected: PASS.

- [x] **Step 9: (Optional, if a local Postgres is available) Run the gated tests**

If the engineer has a local Postgres:
```bash
export SUPERSEDED_POSTGRES_TEST_DSN="postgres://user:pass@localhost/superseded_test"
uv run pytest -m postgres -v
unset SUPERSEDED_POSTGRES_TEST_DSN
```
Expected: PASS (all 9). If no Postgres is available, skip this step — Task 7 includes this as a documented manual check.

- [x] **Step 10: Commit**

```bash
git add src/superseded/memory/postgres.py tests/test_postgres_store.py
git commit -m "feat: add PostgresStore backed by asyncpg pool"
```

---

## Task 5: Add database_url to ServerConfig

**Files:**
- Modify: `src/superseded/server/config.py`
- Modify: `tests/test_server_config.py`

- [x] **Step 1: Write failing tests**

Append to `tests/test_server_config.py` (after the existing tests; reuse the `from_env`-style pattern already in the file — look at `test_server_config_from_env` for the exact monkeypatch setup):

```python
def test_server_config_database_url_defaults_to_none():
    from superseded.server.config import ServerConfig

    assert ServerConfig().database_url is None


def test_server_config_database_url_from_env(monkeypatch, tmp_path):
    from superseded.server.config import ServerConfig

    key = tmp_path / "key.pem"
    key.write_text("x")
    monkeypatch.setenv("SUPERSEDED_APP_ID", "111")
    monkeypatch.setenv("SUPERSEDED_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("SUPERSEDED_PRIVATE_KEY_PATH", str(key))
    monkeypatch.setenv("SUPERSEDED_DATABASE_URL", "postgres://u:p@h/db")
    cfg = ServerConfig.from_env()
    assert cfg.database_url == "postgres://u:p@h/db"


def test_server_config_database_url_absent_from_env(monkeypatch, tmp_path):
    from superseded.server.config import ServerConfig

    key = tmp_path / "key.pem"
    key.write_text("x")
    monkeypatch.setenv("SUPERSEDED_APP_ID", "111")
    monkeypatch.setenv("SUPERSEDED_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("SUPERSEDED_PRIVATE_KEY_PATH", str(key))
    monkeypatch.delenv("SUPERSEDED_DATABASE_URL", raising=False)
    cfg = ServerConfig.from_env()
    assert cfg.database_url is None


def test_server_config_database_url_from_yaml(tmp_path):
    from superseded.server.config import ServerConfig

    key = tmp_path / "key.pem"
    key.write_text("x")
    config_file = tmp_path / "server.yaml"
    config_file.write_text(
        f"app_id: 222\nwebhook_secret: s\nprivate_key_path: {key}\n"
        f"database_url: postgresql://u:p@h/db\n"
    )
    cfg = ServerConfig.from_yaml(config_file)
    assert cfg.database_url == "postgresql://u:p@h/db"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server_config.py -v -k database_url`
Expected: FAIL (`AttributeError: ... 'database_url'` or similar from pydantic).

- [x] **Step 3: Add the field to ServerConfig**

In `src/superseded/server/config.py`, add the field right after `health_token` in the `ServerConfig` class body:

```python
    database_url: str | None = None
```

In `ServerConfig.from_env`, add (near the existing optional reads, e.g. just after the `health_token` block):

```python
        database_url = os.environ.get("SUPERSEDED_DATABASE_URL")
        if database_url:
            kwargs["database_url"] = database_url
```

`ServerConfig.from_yaml` requires no change — `cls(**data)` already passes through unknown YAML keys, and `database_url` is now a known field.

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server_config.py -v`
Expected: PASS (all, including the four new ones).

- [x] **Step 5: Lint, format, full suite**

Run: `uv run ruff check src/superseded/server/config.py tests/test_server_config.py && uv run ruff format src/superseded/server/config.py tests/test_server_config.py && uv run pytest tests/ -q`
Expected: no lint errors; full suite PASS.

- [x] **Step 6: Commit**

```bash
git add src/superseded/server/config.py tests/test_server_config.py
git commit -m "feat: add database_url field to ServerConfig"
```

---

## Task 6: Wire make_store into `serve`, widen store types, close on shutdown

**Files:**
- Modify: `src/superseded/server/worker.py`
- Modify: `src/superseded/server/app.py`
- Modify: `src/superseded/cli.py`

- [x] **Step 1: Widen the store type in worker.py**

In `src/superseded/server/worker.py`:

Replace the TYPE_CHECKING import block (lines 21–24):

```python
if TYPE_CHECKING:
    from superseded.memory.backend import Store
    from superseded.server.github import GitHubApp
    from superseded.server.repo_manager import RepoManager
```

Replace `store: MemoryStore | None = None` in `ReviewWorker.__init__` (line 65) with:

```python
        store: Store | None = None,
```

Replace the type hint of the `store` parameter in `_run_review_for_job` (line 267):

```python
    store: Store | None = None,
```

- [x] **Step 2: Widen the store type in app.py**

In `src/superseded/server/app.py`:

Replace the TYPE_CHECKING import block (lines 12–17):

```python
if TYPE_CHECKING:
    from superseded.memory.backend import Store
    from superseded.server.config import ServerConfig
    from superseded.server.github import GitHubApp
    from superseded.server.repo_manager import RepoManager
    from superseded.server.worker import ReviewWorker
```

Replace the `store: MemoryStore` parameter of `create_app` (line 71) with:

```python
    store: Store,
```

Replace the `store: MemoryStore` parameter of `_handle_pr_event` (line 137) with:

```python
    store: Store,
```

Replace the `store: MemoryStore` parameter of `_handle_installation_event` (line 195) with:

```python
    store: Store,
```

- [x] **Step 3: Wire make_store into the serve command**

In `src/superseded/cli.py`, locate the `serve` function (around line 681). It currently builds two `MemoryStore()` instances at lines 713 and 736.

Replace both occurrences so the function builds a single shared store. Concretely, after `repo_manager = RepoManager(...)` and before `worker = ReviewWorker(...)`, add the import and construct the store:

At the top of `serve`, just inside the function body after the existing local imports, add:

```python
    from superseded.memory.backend import make_store
```

Replace `store=MemoryStore(),` on the `ReviewWorker(...)` call with:

```python
        store=make_store(config.database_url, max_size=config.max_concurrent_reviews + 2),
```

Replace `store=MemoryStore(),` on the `create_app(...)` call with the same shared expression. To avoid constructing two stores, refactor slightly — assign once and reuse:

```python
    store = make_store(config.database_url, max_size=config.max_concurrent_reviews + 2)
    worker = ReviewWorker(
        github=github,
        repo_manager=repo_manager,
        max_concurrent=config.max_concurrent_reviews,
        store=store,
    )
```

…and for `create_app` further down, pass that same `store`:

```python
    app = create_app(
        config=config,
        github=github,
        worker=worker,
        repo_manager=repo_manager,
        store=store,
        lifespan=lifespan,
    )
```

- [x] **Step 4: Close the store on lifespan shutdown**

Still in `cli.py`, the lifespan function (around lines 723–729) currently reads:

```python
    @asynccontextmanager
    async def lifespan(_app):
        await lifecycle.startup()
        try:
            yield
        finally:
            await lifecycle.shutdown()
```

Change it to ensure the store is closed for both backends:

```python
    @asynccontextmanager
    async def lifespan(_app):
        await lifecycle.startup()
        try:
            yield
        finally:
            await lifecycle.shutdown()
            with contextlib.suppress(Exception):
                await store.close()
```

(`contextlib` is already imported at the top of `serve` via `from contextlib import asynccontextmanager`; add a plain `import contextlib` at module level if it is not already present — check first.)

- [x] **Step 5: Verify imports/types still resolve**

Run: `uv run python -c "from superseded.cli import cli; from superseded.server.app import create_app; from superseded.server.worker import ReviewWorker; print('ok')"`
Expected: prints `ok` with no import errors.

- [x] **Step 6: Lint, format, full suite**

Run: `uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run pytest tests/ -q`
Expected: no lint errors; full suite PASS (existing server tests construct `MemoryStore` directly and remain valid).

- [x] **Step 7: Commit**

```bash
git add src/superseded/server/worker.py src/superseded/server/app.py src/superseded/cli.py
git commit -m "feat: wire make_store into serve; widen store types to Store protocol"
```

---

## Task 7: Final verification and documentation

**Files:**
- Modify: `AGENTS.md` (one paragraph under Architecture notes)

- [x] **Step 1: Add an Architecture note about the backend abstraction**

In `AGENTS.md`, under the "## Architecture notes" section, add this bullet after the existing memory-store bullet:

```markdown
- Memory store has two interchangeable backends behind the `Store` Protocol in `memory/backend.py`: `MemoryStore` (SQLite, default, used by the local CLI path) and `PostgresStore` (asyncpg pool, server-only, selected via `ServerConfig.database_url`). `make_store(database_url)` dispatches on URL scheme (`sqlite://`/empty → SQLite, `postgres(ql)://` → Postgres). Postgres tests in `tests/test_postgres_store.py` are `@pytest.mark.postgres` and skipped unless `SUPERSEDED_POSTGRES_TEST_DSN` is set; `addopts = "-m 'not postgres'"` keeps the default `uv run pytest` green without a live DB.
```

- [x] **Step 2: Run the complete verification suite**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest tests/ -v
```
Expected: ruff clean; format check clean; all non-Postgres tests PASS, Postgres tests reported as deselected/skipped.

- [x] **Step 3: Smoke-test the serve wiring with the default (SQLite) backend**

```bash
uv run python -c "
from superseded.memory.backend import make_store
s = make_store(None)
print(type(s).__name__)
assert type(s).__name__ == 'MemoryStore'
print('ok')
"
```
Expected: prints `MemoryStore` then `ok`.

- [x] **Step 4: Smoke-test that an unsupported scheme is rejected**

```bash
uv run python -c "
from superseded.memory.backend import make_store
try:
    make_store('mysql://x')
except ValueError as e:
    print('rejected:', e)
"
```
Expected: `rejected: Unsupported database scheme: 'mysql'`.

- [x] **Step 5: Commit**

```bash
git add AGENTS.md
git commit -m "docs: note Store backend abstraction in AGENTS.md"
```

---

## Self-Review

**Spec coverage** (each spec section → task):
- File layout → Tasks 1–6 each touch exactly the files listed in the spec.
- `Store` Protocol surface → Task 3 (defines all 17 methods).
- `make_store` factory + scheme dispatch → Task 3 (tests cover None/empty/sqlite/postgres/postgresql/unsupported).
- `PostgresStore` schema → Task 4 (full SCHEMA constant, mirrors spec SQL byte-for-byte modulo placeholder style).
- Pooling & lifecycle (`open`/`close`/`init` idempotency, `max_size`) → Task 4 + wired to size in Task 6.
- SQL portability cheat-sheet (each transformation) → Task 4 method bodies implement every transformation.
- Config changes (`database_url` field, env, YAML) → Task 5 (four tests).
- StatsAggregator refactor (move SQL into store, keep policy) → Task 2.
- CLI wiring (single shared store, lifespan close) → Task 6.
- Error handling (let asyncpg errors propagate, ValueError on bad scheme) → Task 4 (no try/except wrapping) + Task 3 (`make_store` raises).
- Testing strategy (gated `@pytest.mark.postgres`, default `addopts`) → Task 1 (config) + Task 4 (tests).
- Packaging (`asyncpg` hard dep) → Task 1.
- Back-compat (`MemoryStore` unchanged behaviorally; CLI path unchanged) → Tasks 2 (additive only) + 6 (CLI commands other than `serve` untouched).

**Placeholder scan:** No TBD/TODO/"add appropriate …"/"similar to Task N" found. Every code step contains the actual code.

**Type consistency:**
- `make_store(database_url, *, max_size=None) -> Store` — used identically in Task 3 tests, Task 4 tests, Task 6 wiring.
- `PostgresStore(dsn, *, min_size=1, max_size=None)` — matches both the Task 3 mock assertion (`PostgresStore("postgres://...", max_size=5)`) and Task 4 fixture (`PostgresStore(_DSN, max_size=4)`).
- `Store.get_review_stats(repo, min_sample)` and `Store.refresh_review_stats(repo)` — identical signatures in Task 2 (`MemoryStore`), Task 3 (Protocol), Task 4 (`PostgresStore`).
- `_STATS_FILE_PATTERN_CASE` — defined as a module constant in both `store.py` (Task 2, SQLite `%`-doubling) and `postgres.py` (Task 4, single `%`).

No issues found. Plan is ready.
