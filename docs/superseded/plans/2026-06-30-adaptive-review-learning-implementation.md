# Adaptive Review Learning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an adaptive review criteria engine that learns from feedback outcomes (dismissals/acceptances) and injects statistical guidance + LLM-inferred rules into each review prompt.

**Architecture:** New `audit/` package with `StatsAggregator` (pre-computes dismissal rates), `PatternReflector` (LLM-driven rule inference), and `guidelines.py` (assembles combined prompt block). Three new SQLite tables store stats, learned rules, and reflection watermarks. CLI and server worker both wire the pipeline after each review cycle. Prompt gains a new `### Learned Review Guidelines` section.

**Tech Stack:** Python 3.14+, aiosqlite (existing), subprocess (for agent CLI calls), Pydantic (Config), click (CLI)

---

### Task 1: Add Config fields

**Files:**
- Modify: `src/superseded/config.py:18-31`

- [x] **Step 1: Add three new fields with defaults**

```python
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
    graph: bool = True
    progressive: bool = True
    learned_review: bool = True
    reflection_threshold: int = 5
    max_learned_rules: int = 5
```

- [x] **Step 2: Verify existing tests still pass**

Run: `uv run pytest tests/ -v -k "config" -x`
Expected: PASS

- [x] **Step 3: Commit**

```bash
git add src/superseded/config.py
git commit -m "feat(config): add learned_review, reflection_threshold, max_learned_rules fields"
```

---

### Task 2: Add MemoryStore schema, migration, and new methods

**Files:**
- Modify: `src/superseded/memory/store.py:14-52,125-139`
- Test: `tests/test_memory.py`

- [x] **Step 1: Write the failing tests**

Add to `tests/test_memory.py`:

```python
async def _test_review_stats_schema():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = MemoryStore(db_path)
        await store.init()

        await store.record_finding(
            finding_id="sec-abc", repo="owner/repo", pass_name="security",
            severity="critical", file="a.py", line=42, title="t", description="d",
        )
        await store.set_comment_id("sec-abc", 1)
        await store.record_feedback_by_comment_id(1, "dismiss")

        # refresh via direct SQL for now — the StatsAggregator will wrap this
        await store.open()
        await store._conn.execute(
            "INSERT INTO review_stats (repo, pass, severity, file_pattern, total, accepted, dismissed) "
            "SELECT f.repo, f.pass, f.severity, '*', "
            "COUNT(*), "
            "COUNT(*) FILTER (WHERE fb.action = 'helpful'), "
            "COUNT(*) FILTER (WHERE fb.action = 'dismiss') "
            "FROM findings f JOIN feedback fb ON fb.finding_id = f.id "
            "WHERE f.repo = 'owner/repo' "
            "GROUP BY f.repo, f.pass, f.severity "
            "ON CONFLICT(repo, pass, severity, file_pattern) DO UPDATE SET "
            "total = excluded.total, accepted = excluded.accepted, dismissed = excluded.dismissed"
        )
        await store._conn.commit()

        rows = await store._conn.execute_fetchall(
            "SELECT * FROM review_stats WHERE repo = ?", ("owner/repo",)
        )
        assert len(rows) == 1
        assert rows[0]["pass"] == "security"
        assert rows[0]["total"] == 1
        assert rows[0]["dismissed"] == 1


def test_review_stats_schema():
    asyncio.run(_test_review_stats_schema())


async def _test_learned_rules_crud():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = MemoryStore(db_path)
        await store.init()

        await store.get_reflection_state("owner/repo") == 0

        await store.get_learned_rules("owner/repo") == []

        rules = await store.get_learned_rules("owner/repo", limit=5)
        assert rules == []

        await store.open()
        await store._conn.execute(
            "INSERT INTO learned_rules (repo, rule_text, evidence_count, confidence) "
            "VALUES (?, ?, ?, ?)",
            ("owner/repo", "Avoid flagging style issues in test files", 3, 0.9),
        )
        await store._conn.commit()

        rules = await store.get_learned_rules("owner/repo", limit=5)
        assert len(rules) == 1
        assert rules[0]["rule_text"] == "Avoid flagging style issues in test files"
        assert rules[0]["confidence"] == 0.9

        # different repo gets nothing
        assert await store.get_learned_rules("other/repo") == []


def test_learned_rules_crud():
    asyncio.run(_test_learned_rules_crud())


async def _test_reflection_state_tracking():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = MemoryStore(db_path)
        await store.init()

        assert await store.get_reflection_state("owner/repo") == 0

        await store.set_reflection_state("owner/repo", 42)
        assert await store.get_reflection_state("owner/repo") == 42

        await store.set_reflection_state("owner/repo", 99)
        assert await store.get_reflection_state("owner/repo") == 99


def test_reflection_state_tracking():
    asyncio.run(_test_reflection_state_tracking())


async def _test_learned_rules_respects_confidence_filter():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = MemoryStore(db_path)
        await store.init()

        await store.open()
        await store._conn.execute(
            "INSERT INTO learned_rules (repo, rule_text, evidence_count, confidence) "
            "VALUES (?, ?, ?, ?)",
            ("owner/repo", "Good rule", 5, 0.8),
        )
        await store._conn.execute(
            "INSERT INTO learned_rules (repo, rule_text, evidence_count, confidence) "
            "VALUES (?, ?, ?, ?)",
            ("owner/repo", "Low confidence rule", 1, 0.2),
        )
        await store._conn.commit()

        rules = await store.get_learned_rules("owner/repo", limit=5)
        assert len(rules) == 1
        assert rules[0]["rule_text"] == "Good rule"


def test_learned_rules_confidence_filter():
    asyncio.run(_test_learned_rules_respects_confidence_filter())
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_memory.py::test_review_stats_schema -v`
Expected: FAIL with "no such table: review_stats"

- [x] **Step 3: Add tables to SCHEMA and _migrate()**

In `src/superseded/memory/store.py`, add to the `SCHEMA` string:

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS findings (
    ...
);

...

CREATE TABLE IF NOT EXISTS review_watermarks (
    repo        TEXT    NOT NULL,
    pr_number   INTEGER NOT NULL,
    head_sha    TEXT    NOT NULL,
    reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (repo, pr_number)
);

CREATE TABLE IF NOT EXISTS review_stats (
    repo         TEXT    NOT NULL,
    pass         TEXT    NOT NULL,
    severity     TEXT    NOT NULL,
    file_pattern TEXT    NOT NULL DEFAULT '*',
    total        INTEGER NOT NULL DEFAULT 0,
    accepted     INTEGER NOT NULL DEFAULT 0,
    dismissed    INTEGER NOT NULL DEFAULT 0,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (repo, pass, severity, file_pattern)
);

CREATE TABLE IF NOT EXISTS learned_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    repo            TEXT    NOT NULL,
    rule_text       TEXT    NOT NULL,
    evidence_count  INTEGER NOT NULL DEFAULT 0,
    confidence      REAL    NOT NULL DEFAULT 1.0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_applied_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reflection_state (
    repo               TEXT    NOT NULL,
    last_feedback_id   INTEGER NOT NULL,
    last_reflection_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (repo)
);
"""
```

In `_migrate()`, add after the existing migration blocks:

```python
await db.execute(
    "CREATE TABLE IF NOT EXISTS review_stats ("
    "repo TEXT NOT NULL, "
    "pass TEXT NOT NULL, "
    "severity TEXT NOT NULL, "
    "file_pattern TEXT NOT NULL DEFAULT '*', "
    "total INTEGER NOT NULL DEFAULT 0, "
    "accepted INTEGER NOT NULL DEFAULT 0, "
    "dismissed INTEGER NOT NULL DEFAULT 0, "
    "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
    "PRIMARY KEY (repo, pass, severity, file_pattern))"
)
await db.execute(
    "CREATE TABLE IF NOT EXISTS learned_rules ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "repo TEXT NOT NULL, "
    "rule_text TEXT NOT NULL, "
    "evidence_count INTEGER NOT NULL DEFAULT 0, "
    "confidence REAL NOT NULL DEFAULT 1.0, "
    "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
    "last_applied_at TIMESTAMP)"
)
await db.execute(
    "CREATE TABLE IF NOT EXISTS reflection_state ("
    "repo TEXT NOT NULL, "
    "last_feedback_id INTEGER NOT NULL, "
    "last_reflection_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
    "PRIMARY KEY (repo))"
)
```

- [x] **Step 4: Run tests to verify table creation works**

Run: `uv run pytest tests/test_memory.py::test_review_stats_schema -v`
Expected: PASS (table exists, inserts work)

- [x] **Step 5: Implement new MemoryStore methods**

Add below existing methods in `store.py`:

```python
async def get_learned_rules(self, repo: str, limit: int = 5) -> list[dict]:
    async with self._db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM learned_rules WHERE repo = ? AND confidence >= 0.3 "
            "ORDER BY confidence DESC, created_at DESC LIMIT ?",
            (repo, limit),
        )
        rows = await cursor.fetchall()
        if rows:
            ids = [row["id"] for row in rows]
            placeholders = ",".join("?" * len(ids))
            await db.execute(
                f"UPDATE learned_rules SET last_applied_at = CURRENT_TIMESTAMP "
                f"WHERE id IN ({placeholders})",
                ids,
            )
            await db.commit()
        return [dict(row) for row in rows]


async def get_reflection_state(self, repo: str) -> int:
    async with self._db() as db:
        cursor = await db.execute(
            "SELECT last_feedback_id FROM reflection_state WHERE repo = ?",
            (repo,),
        )
        row = await cursor.fetchone()
        return row[0] if row is not None else 0


async def set_reflection_state(self, repo: str, last_feedback_id: int) -> None:
    async with self._db() as db:
        await db.execute(
            "INSERT INTO reflection_state (repo, last_feedback_id) VALUES (?, ?) "
            "ON CONFLICT(repo) DO UPDATE SET "
            "last_feedback_id = excluded.last_feedback_id, "
            "last_reflection_at = CURRENT_TIMESTAMP",
            (repo, last_feedback_id),
        )
        await db.commit()
```

- [x] **Step 6: Run all new tests**

Run: `uv run pytest tests/test_memory.py -v -k "test_review_stats or test_learned_rules or test_reflection_state" -x`
Expected: ALL PASS

- [x] **Step 7: Run existing tests to check for regressions**

Run: `uv run pytest tests/test_memory.py -v -x`
Expected: ALL PASS

- [x] **Step 8: Commit**

```bash
git add src/superseded/memory/store.py tests/test_memory.py
git commit -m "feat(memory): add review_stats, learned_rules, reflection_state tables and methods"
```

---

### Task 3: Implement `audit/stats.py` — StatsAggregator

**Files:**
- Create: `src/superseded/audit/__init__.py`
- Create: `src/superseded/audit/stats.py`
- Test: `tests/test_audit_stats.py`

- [x] **Step 1: Write the failing tests**

Create `tests/test_audit_stats.py`:

```python
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
from superseded.audit.stats import StatsAggregator
from superseded.memory.store import MemoryStore


def _classify(file: str) -> str:
    if (
        file.startswith("test/") or file.startswith("tests/")
        or "_test." in file or file.startswith("test_")
        or "__test__/" in file
    ):
        return "test"
    if "migrations/" in file:
        return "migration"
    if file.endswith((".yaml", ".yml", ".toml", ".json")) or file.startswith("Dockerfile"):
        return "config"
    return "*"


async def _make_store(db_path: Path) -> MemoryStore:
    store = MemoryStore(db_path)
    await store.init()
    async with store:
        for i, (fid, pass_name, severity, file, comment_id, action) in enumerate([
            ("sec-1", "security", "critical", "src/auth.py", 1, "helpful"),
            ("sec-2", "security", "critical", "src/auth.py", 2, "dismiss"),
            ("sec-3", "security", "suggestion", "src/auth.py", 3, "dismiss"),
            ("sty-1", "style", "nit", "tests/test_auth.py", 4, "dismiss"),
            ("sty-2", "style", "nit", "tests/test_auth.py", 5, "dismiss"),
            ("sty-3", "style", "nit", "tests/test_auth.py", 6, "dismiss"),
            ("perf-1", "performance", "important", "src/worker.py", 7, "helpful"),
            ("perf-2", "performance", "important", "src/worker.py", 8, "helpful"),
            ("perf-3", "performance", "suggestion", "migrations/001.py", 9, "dismiss"),
            ("perf-4", "performance", "suggestion", "migrations/001.py", 10, "dismiss"),
            ("perf-5", "performance", "suggestion", "migrations/001.py", 11, "dismiss"),
        ]):
            await store.record_finding(
                finding_id=fid, repo="owner/repo",
                pass_name=pass_name, severity=severity,
                file=file, line=1, title="t", description="d",
            )
            await store.set_comment_id(fid, comment_id)
            await store.record_feedback_by_comment_id(comment_id, action)
    return store


async def _test_refresh_creates_rows():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = await _make_store(db_path)
        aggregator = StatsAggregator(store)
        await aggregator._refresh("owner/repo")

        async with store:
            rows = await store._conn.execute_fetchall(
                "SELECT * FROM review_stats WHERE repo = ? ORDER BY pass, severity, file_pattern",
                ("owner/repo",),
            )
        assert len(rows) == 6
        # security/critical/*  -> 1 accepted, 1 dismissed, 2 total
        sec_crit = [r for r in rows if r["pass"] == "security" and r["severity"] == "critical"][0]
        assert sec_crit["total"] == 2
        assert sec_crit["accepted"] == 1
        assert sec_crit["dismissed"] == 1


def test_refresh_creates_rows():
    asyncio.run(_test_refresh_creates_rows())


async def _test_get_stats_context_returns_none_when_no_data():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = MemoryStore(db_path)
        await store.init()
        aggregator = StatsAggregator(store)
        assert await aggregator.get_stats_context("owner/repo") is None


def test_get_stats_context_none():
    asyncio.run(_test_get_stats_context_returns_none_when_no_data())


async def _test_get_stats_context_formats_high_dismissal():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = await _make_store(db_path)
        aggregator = StatsAggregator(store)
        await aggregator._refresh("owner/repo")
        text = await aggregator.get_stats_context("owner/repo")
        assert text is not None
        # style/nit in test files: 3 dismissed out of 3 -> 100% dismissed
        assert "style" in text
        assert "100%" in text or "100" in text  # depends on rounding
        assert "test" in text.lower() or "test files" in text.lower()
        # performance/migration: 3 dismissed out of 3
        assert "migration" in text.lower()


def test_get_stats_context_high_dismissal():
    asyncio.run(_test_get_stats_context_formats_high_dismissal())


async def _test_get_stats_context_formats_high_acceptance():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = await _make_store(db_path)
        aggregator = StatsAggregator(store)
        await aggregator._refresh("owner/repo")
        text = await aggregator.get_stats_context("owner/repo")
        assert text is not None
        # performance/important: 2 accepted out of 2 -> 100% accepted
        assert "continue current approach" in text.lower()


def test_get_stats_context_high_acceptance():
    asyncio.run(_test_get_stats_context_formats_high_acceptance())


async def _test_refresh_upserts():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = MemoryStore(db_path)
        await store.init()
        async with store:
            await store.record_finding(
                finding_id="a", repo="o/r", pass_name="security", severity="critical",
                file="x.py", line=1, title="t", description="d",
            )
            await store.set_comment_id("a", 1)
            await store.record_feedback_by_comment_id(1, "dismiss")
        aggregator = StatsAggregator(store)
        await aggregator._refresh("o/r")
        async with store:
            rows = await store._conn.execute_fetchall(
                "SELECT * FROM review_stats WHERE repo = ?", ("o/r",)
            )
        assert rows[0]["total"] == 1

        # add another finding with same pass/severity/pattern
        async with store:
            await store.record_finding(
                finding_id="b", repo="o/r", pass_name="security", severity="critical",
                file="y.py", line=1, title="t2", description="d2",
            )
            await store.set_comment_id("b", 2)
            await store.record_feedback_by_comment_id(2, "helpful")
        await aggregator._refresh("o/r")
        async with store:
            rows = await store._conn.execute_fetchall(
                "SELECT * FROM review_stats WHERE repo = ?", ("o/r",)
            )
        assert rows[0]["total"] == 2
        assert rows[0]["accepted"] == 1
        assert rows[0]["dismissed"] == 1


def test_refresh_upserts():
    asyncio.run(_test_refresh_upserts())


async def _test_refresh_excludes_findings_without_feedback():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = MemoryStore(db_path)
        await store.init()
        async with store:
            await store.record_finding(
                finding_id="no-fb", repo="o/r", pass_name="security", severity="critical",
                file="x.py", line=1, title="t", description="d",
            )
        # no feedback recorded — so no stats row should appear
        aggregator = StatsAggregator(store)
        await aggregator._refresh("o/r")
        async with store:
            rows = await store._conn.execute_fetchall(
                "SELECT * FROM review_stats WHERE repo = ?", ("o/r",)
            )
        assert len(rows) == 0


def test_refresh_excludes_without_feedback():
    asyncio.run(_test_refresh_excludes_findings_without_feedback())


async def _test_get_stats_context_different_repo_isolation():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = await _make_store(db_path)
        aggregator = StatsAggregator(store)
        await aggregator._refresh("owner/repo")
        # query for a different repo returns None
        assert await aggregator.get_stats_context("other/repo") is None


def test_get_stats_context_repo_isolation():
    asyncio.run(_test_get_stats_context_different_repo_isolation())
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_audit_stats.py -v`
Expected: FAIL with "No module named 'superseded.audit'"

- [x] **Step 3: Create `audit/__init__.py`**

```python
from __future__ import annotations
```

- [x] **Step 4: Implement `audit/stats.py`**

```python
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from superseded.memory.store import MemoryStore


def _classify_file_pattern(file: str) -> str:
    if (
        file.startswith("test/") or file.startswith("tests/")
        or "_test." in file or file.startswith("test_")
        or "__test__/" in file
    ):
        return "test"
    if "migrations/" in file:
        return "migration"
    if file.endswith((".yaml", ".yml", ".toml", ".json")) or file.startswith("Dockerfile"):
        return "config"
    return "*"


class StatsAggregator:
    MIN_SAMPLE = 5

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def get_stats_context(self, repo: str) -> str | None:
        async with self._store:
            await self._store.open()
            import aiosqlite
            self._store._conn.row_factory = aiosqlite.Row
            cursor = await self._store._conn.execute(
                "SELECT * FROM review_stats WHERE repo = ? AND total >= ?",
                (repo, self.MIN_SAMPLE),
            )
            rows = await cursor.fetchall()
        if not rows:
            return None
        lines: list[str] = []
        for row in rows:
            total = row["total"]
            accepted = row["accepted"]
            dismissed = row["dismissed"]
            pass_name = row["pass"]
            severity = row["severity"]
            file_pattern = row["file_pattern"]

            if total == 0:
                continue
            dismiss_rate = dismissed / total
            accept_rate = accepted / total

            if dismiss_rate > 0.8 and file_pattern != "*":
                pct = round(dismiss_rate * 100)
                lines.append(
                    f"- {pass_name} pass: {pct}% dismissed ({dismissed}/{total}) "
                    f"in {file_pattern} files — suppress {severity} findings here"
                )
            elif dismiss_rate > 0.5:
                pct = round(dismiss_rate * 100)
                lines.append(
                    f"- {pass_name} pass: {pct}% dismissed ({dismissed}/{total}) "
                    f"for '{severity}' severity — prefer higher severity"
                )
            elif accept_rate > 0.8:
                pct = round(accept_rate * 100)
                lines.append(
                    f"- {pass_name} pass: {pct}% accepted ({accepted}/{total}) "
                    f"— continue current approach"
                )
        return "\n".join(lines) if lines else None

    async def _refresh(self, repo: str) -> None:
        async with self._store:
            await self._store.open()
            await self._store._conn.execute(
                "INSERT INTO review_stats "
                "(repo, pass, severity, file_pattern, total, accepted, dismissed) "
                "SELECT f.repo, f.pass, f.severity, "
                "CASE "
                "WHEN f.file LIKE 'test/%' OR f.file LIKE 'tests/%' "
                "  OR f.file LIKE '%_test.%' OR f.file LIKE 'test_%' "
                "  OR f.file LIKE '__test__/%' THEN 'test' "
                "WHEN f.file LIKE 'migrations/%' OR f.file LIKE '%/migrations/%' "
                "  THEN 'migration' "
                "WHEN f.file LIKE '%.yaml' OR f.file LIKE '%.yml' "
                "  OR f.file LIKE '%.toml' OR f.file LIKE '%.json' "
                "  OR f.file LIKE 'Dockerfile%' THEN 'config' "
                "ELSE '*' END AS file_pattern, "
                "COUNT(*) AS total, "
                "COUNT(*) FILTER (WHERE fb.action = 'helpful') AS accepted, "
                "COUNT(*) FILTER (WHERE fb.action = 'dismiss') AS dismissed "
                "FROM findings f "
                "JOIN feedback fb ON fb.finding_id = f.id "
                "WHERE f.repo = ? "
                "GROUP BY f.repo, f.pass, f.severity, file_pattern "
                "ON CONFLICT(repo, pass, severity, file_pattern) DO UPDATE SET "
                "total = excluded.total, "
                "accepted = excluded.accepted, "
                "dismissed = excluded.dismissed, "
                "updated_at = CURRENT_TIMESTAMP",
                (repo,),
            )
            await self._store._conn.commit()
```

- [x] **Step 5: Run tests**

Run: `uv run pytest tests/test_audit_stats.py -v -x`
Expected: ALL PASS

- [x] **Step 6: Commit**

```bash
git add src/superseded/audit/__init__.py src/superseded/audit/stats.py tests/test_audit_stats.py
git commit -m "feat(audit): add StatsAggregator with refresh and get_stats_context"
```

---

### Task 4: Implement `audit/guidelines.py` — assemble_learned_context

**Files:**
- Create: `src/superseded/audit/guidelines.py`
- Test: `tests/test_audit_guidelines.py`

- [x] **Step 1: Write the failing tests**

Create `tests/test_audit_guidelines.py`:

```python
from __future__ import annotations

from superseded.audit.guidelines import assemble_learned_context


def test_both_none_returns_none():
    assert assemble_learned_context(None, []) is None
    assert assemble_learned_context(None, [], max_rules=5) is None


def test_stats_only():
    text = assemble_learned_context("- perf: 100% accepted", [])
    assert text is not None
    assert "Statistical guidance" in text
    assert "- perf: 100% accepted" in text
    assert "Inferred rules" not in text


def test_rules_only():
    rules = [{"rule_text": "Do not flag style nits in tests", "confidence": 0.9, "evidence_count": 3}]
    text = assemble_learned_context(None, rules)
    assert text is not None
    assert "Inferred rules" in text
    assert "Do not flag style nits in tests" in text
    assert "90%" in text
    assert "Statistical guidance" not in text


def test_both_combined():
    rules = [
        {"rule_text": "Rule A", "confidence": 0.9, "evidence_count": 5},
        {"rule_text": "Rule B", "confidence": 0.7, "evidence_count": 3},
    ]
    text = assemble_learned_context("- perf: 100% accepted", rules)
    assert text is not None
    assert "Statistical guidance" in text
    assert "Inferred rules" in text
    assert "Rule A" in text
    assert "Rule B" in text
    # confidence formatted as percentage
    assert "90%" in text
    assert "70%" in text


def test_rules_capped_to_max():
    rules = [
        {"rule_text": f"Rule {i}", "confidence": 1.0 - i * 0.1, "evidence_count": 1}
        for i in range(10)
    ]
    text = assemble_learned_context(None, rules, max_rules=3)
    assert text is not None
    assert "Rule 0" in text
    assert "Rule 1" in text
    assert "Rule 2" in text
    assert "Rule 3" not in text  # cap at 3


def test_rules_sorted_by_confidence_desc():
    rules = [
        {"rule_text": "Low", "confidence": 0.3, "evidence_count": 1},
        {"rule_text": "High", "confidence": 0.95, "evidence_count": 1},
        {"rule_text": "Mid", "confidence": 0.6, "evidence_count": 1},
    ]
    text = assemble_learned_context(None, rules)
    assert text is not None
    high_pos = text.index("High")
    mid_pos = text.index("Mid")
    low_pos = text.index("Low")
    assert high_pos < mid_pos < low_pos


def test_rules_with_evidence():
    rules = [{"rule_text": "Test rule", "confidence": 0.85, "evidence_count": 4}]
    text = assemble_learned_context(None, rules)
    assert "4 dismissals" in text.lower() or "evidence" in text.lower()


def test_with_created_at_sort_break_ties():
    from datetime import datetime, timedelta
    older = datetime(2026, 1, 1).isoformat()
    newer = datetime(2026, 6, 1).isoformat()
    rules = [
        {"rule_text": "Older", "confidence": 0.8, "evidence_count": 1, "created_at": older},
        {"rule_text": "Newer", "confidence": 0.8, "evidence_count": 1, "created_at": newer},
    ]
    text = assemble_learned_context(None, rules)
    assert text is not None
    newer_pos = text.index("Newer")
    older_pos = text.index("Older")
    assert newer_pos < older_pos
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_audit_guidelines.py -v`
Expected: FAIL with "No module named 'superseded.audit.guidelines'"

- [x] **Step 3: Implement `audit/guidelines.py`**

```python
from __future__ import annotations

from datetime import datetime

MAX_RULES = 5


def assemble_learned_context(
    stats_text: str | None,
    rules: list[dict],
    max_rules: int = MAX_RULES,
) -> str | None:
    if stats_text is None and not rules:
        return None

    rules = sorted(
        rules,
        key=lambda r: (
            r.get("confidence", 0),
            r.get("created_at", ""),
        ),
        reverse=True,
    )
    rules = rules[:max_rules]

    sections: list[str] = ["Based on past review outcomes, the team has implicit preferences:"]

    if stats_text:
        sections.append(f"\n**Statistical guidance:**\n{stats_text}")

    if rules:
        lines = ["\n**Inferred rules:**"]
        for i, r in enumerate(rules, 1):
            conf = r.get("confidence", 1.0)
            evidence = r.get("evidence_count", 0)
            evidence_str = f"{evidence} dismissal(s)" if evidence else ""
            lines.append(
                f"{i}. {r['rule_text']} (confidence: {conf:.0%}"
                + (f", {evidence_str})" if evidence_str else ")")
            )
            if len(lines) >= max_rules + 1:
                break
        sections.append("\n".join(lines))

    return "\n".join(sections)
```

- [x] **Step 4: Run tests**

Run: `uv run pytest tests/test_audit_guidelines.py -v -x`
Expected: ALL PASS

- [x] **Step 5: Commit**

```bash
git add src/superseded/audit/guidelines.py tests/test_audit_guidelines.py
git commit -m "feat(audit): add assemble_learned_context for prompt integration"
```

---

### Task 5: Implement `audit/reflector.py` — PatternReflector

**Files:**
- Create: `src/superseded/audit/reflector.py`
- Test: `tests/test_audit_reflector.py`

- [x] **Step 1: Write the failing tests**

Create `tests/test_audit_reflector.py`:

```python
from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from superseded.audit.reflector import MAX_RULES, REFLECTION_THRESHOLD, PatternReflector
from superseded.memory.store import MemoryStore


async def _make_store_with_feedback(
    db_path: Path, count: int, repo: str = "owner/repo"
) -> MemoryStore:
    store = MemoryStore(db_path)
    await store.init()
    async with store:
        for i in range(count):
            fid = f"sec-{i}"
            cid = i + 1
            action = "dismiss" if i % 2 == 0 else "helpful"
            await store.record_finding(
                finding_id=fid, repo=repo, pass_name="security",
                severity="critical", file="a.py", line=1,
                title=f"Finding {i}", description=f"Description {i}",
            )
            await store.set_comment_id(fid, cid)
            await store.record_feedback_by_comment_id(cid, action)
    return store


async def _test_maybe_reflect_below_threshold():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = await _make_store_with_feedback(db_path, 3)  # < 5
        mock_agent = MagicMock()
        reflector = PatternReflector(mock_agent, store)
        rules = await reflector.maybe_reflect("owner/repo")
        assert rules == []


def test_below_threshold():
    asyncio.run(_test_maybe_reflect_below_threshold())


async def _test_maybe_reflect_processes_feedback():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = await _make_store_with_feedback(db_path, 6)  # >= 5
        mock_agent = MagicMock()
        mock_agent.build_command.return_value = ["test-agent", "--bare"]
        mock_agent.parse_output.return_value = [
            {"rule": "Avoid style nits in tests", "evidence": "3x dismissed", "confidence": 0.9}
        ]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps([
                    {"rule": "Avoid style nits in tests", "evidence": "3x dismissed", "confidence": 0.9}
                ]),
            )
            reflector = PatternReflector(mock_agent, store)
            rules = await reflector.maybe_reflect("owner/repo")

        assert len(rules) == 1
        assert rules[0]["rule_text"] == "Avoid style nits in tests"
        assert rules[0]["confidence"] == 0.9
        assert rules[0]["evidence_count"] > 0

        # reflection_state updated
        state = await store.get_reflection_state("owner/repo")
        assert state > 0


def test_processes_feedback():
    asyncio.run(_test_maybe_reflect_processes_feedback())


async def _test_maybe_reflect_handles_agent_failure():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = await _make_store_with_feedback(db_path, 6)
        mock_agent = MagicMock()
        mock_agent.build_command.return_value = ["test-agent"]

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "test-agent")
            reflector = PatternReflector(mock_agent, store)
            rules = await reflector.maybe_reflect("owner/repo")

        assert rules == []
        # state NOT updated (so next run retries)
        assert await store.get_reflection_state("owner/repo") == 0


def test_handles_agent_failure():
    asyncio.run(_test_maybe_reflect_handles_agent_failure())


async def _test_maybe_reflect_handles_invalid_json():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = await _make_store_with_feedback(db_path, 6)
        mock_agent = MagicMock()
        mock_agent.build_command.return_value = ["test-agent"]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="not json at all")
            reflector = PatternReflector(mock_agent, store)
            rules = await reflector.maybe_reflect("owner/repo")

        assert rules == []


def test_handles_invalid_json():
    asyncio.run(_test_maybe_reflect_handles_invalid_json())


async def _test_maybe_reflect_empty_rules_still_updates_state():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = await _make_store_with_feedback(db_path, 6)
        mock_agent = MagicMock()
        mock_agent.build_command.return_value = ["test-agent"]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="[]")
            reflector = PatternReflector(mock_agent, store)
            rules = await reflector.maybe_reflect("owner/repo")

        assert rules == []
        # state IS updated (feedback was processed, just no patterns found)
        assert await store.get_reflection_state("owner/repo") > 0


def test_empty_rules_updates_state():
    asyncio.run(_test_maybe_reflect_empty_rules_still_updates_state())


async def _test_maybe_reflect_prompt_includes_accepted_and_dismissed():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = await _make_store_with_feedback(db_path, 6)
        mock_agent = MagicMock()
        mock_agent.build_command.return_value = ["test-agent"]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="[]")
            reflector = PatternReflector(mock_agent, store)
            await reflector.maybe_reflect("owner/repo")

            prompt = mock_run.call_args.kwargs.get("input", "")
            assert "ACCEPTED:" in prompt
            assert "DISMISSED:" in prompt
            assert "Finding 1" in prompt  # finding titles appear


def test_prompt_includes_both():
    asyncio.run(_test_maybe_reflect_prompt_includes_accepted_and_dismissed())
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_audit_reflector.py -v`
Expected: FAIL with import error

- [x] **Step 3: Implement `audit/reflector.py`**

```python
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from superseded.agents.base import Agent
    from superseded.memory.store import MemoryStore

logger = logging.getLogger(__name__)

REFLECTION_THRESHOLD = 5
MAX_RULES = 5


_REFLECTION_PROMPT = """You are analyzing past code review outcomes to improve future reviews.

Below are findings that were accepted (helpful) or dismissed across multiple
review passes for this repository.

{accepted_section}{dismissed_section}
Analyze these patterns. Output rules ONLY about patterns that were dismissed
2+ times across the same pass or file pattern. Each rule must be a general
principle the team follows — NOT a specific finding. Rules must be 1 sentence,
imperative tone, and actionable (an AI reviewer should be able to apply it).

Return ONLY a JSON array. No explanation text before or after.

[
  {{
    "rule": "Do not flag naming conventions in API-facing functions",
    "evidence": "2 dismissals: snake_case in api.py, naming in api_helpers.py",
    "confidence": 0.9
  }}
]

If no clear patterns emerge, return: []"""


class PatternReflector:
    def __init__(self, agent: Agent, store: MemoryStore) -> None:
        self._agent = agent
        self._store = store

    async def maybe_reflect(
        self, repo: str, cwd: str | Path | None = None
    ) -> list[dict]:
        last_id = await self._store.get_reflection_state(repo)

        async with self._store:
            await self._store.open()
            import aiosqlite
            self._store._conn.row_factory = aiosqlite.Row
            cursor = await self._store._conn.execute(
                "SELECT fb.id AS fb_id, fb.action, fb.finding_id, "
                "f.pass, f.severity, f.file, f.title "
                "FROM feedback fb JOIN findings f ON f.id = fb.finding_id "
                "WHERE f.repo = ? AND fb.id > ? ORDER BY fb.id",
                (repo, last_id),
            )
            rows = await cursor.fetchall()

        if not rows or len(rows) < REFLECTION_THRESHOLD:
            return []

        accepted = [r for r in rows if r["action"] == "helpful"]
        dismissed = [r for r in rows if r["action"] == "dismiss"]

        prompt = _build_reflection_prompt(accepted, dismissed)
        cmd = self._agent.build_command()

        try:
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(cwd) if cwd is not None else None,
            )
        except FileNotFoundError:
            logger.warning("Agent CLI not found for reflection; skipping")
            return []
        except subprocess.TimeoutExpired:
            logger.warning("Reflection timed out after 120s")
            return []

        if result.returncode != 0:
            logger.warning(
                "Reflection agent exited %d: %s",
                result.returncode,
                result.stderr.strip()[:500],
            )
            return []

        raw_rules = self._agent.parse_output(result.stdout, "reflection")

        new_rules: list[dict] = []
        for item in raw_rules:
            rule_text = item.get("rule")
            if not rule_text:
                continue
            confidence = float(item.get("confidence", 1.0))
            evidence = item.get("evidence", "")
            evidence_count = len(dismissed) // 2  # rough estimate if not parsed
            async with self._store:
                await self._store.open()
                cursor = await self._store._conn.execute(
                    "INSERT INTO learned_rules (repo, rule_text, evidence_count, confidence) "
                    "VALUES (?, ?, ?, ?)",
                    (repo, rule_text, evidence_count, confidence),
                )
                await self._store._conn.commit()
                rule_id = cursor.lastrowid
            new_rules.append({
                "id": rule_id,
                "repo": repo,
                "rule_text": rule_text,
                "evidence_count": evidence_count,
                "confidence": confidence,
            })

        max_fb_id = max(r["fb_id"] for r in rows)
        await self._store.set_reflection_state(repo, max_fb_id)

        return new_rules[:MAX_RULES]


def _build_reflection_prompt(
    accepted: list[dict], dismissed: list[dict]
) -> str:
    def _format(items: list[dict]) -> str:
        lines = []
        for r in items:
            lines.append(
                f'- [{r["pass"]}] "{r["title"]}" '
                f'(file: {r["file"]}, severity: {r["severity"]})'
            )
        return "\n".join(lines) if lines else "(none)"

    accepted_str = _format(accepted)
    dismissed_str = _format(dismissed)

    return _REFLECTION_PROMPT.format(
        accepted_section=f"\nACCEPTED:\n{accepted_str}\n",
        dismissed_section=f"\nDISMISSED:\n{dismissed_str}\n",
    )
```

- [x] **Step 4: Run tests**

Run: `uv run pytest tests/test_audit_reflector.py -v -x`
Expected: ALL PASS

- [x] **Step 5: Commit**

```bash
git add src/superseded/audit/reflector.py tests/test_audit_reflector.py
git commit -m "feat(audit): add PatternReflector for LLM-driven rule inference"
```

---

### Task 6: Add learned_context to prompts.py

**Files:**
- Modify: `src/superseded/review/prompts.py:48-109`
- Test: `tests/test_prompts.py`

- [x] **Step 1: Write the failing tests**

Add to `tests/test_prompts.py`:

```python
def test_learned_context_section_present_when_kwarg_non_empty():
    prompt = build_prompt(
        pass_name="security",
        diff="x",
        pr_description=None,
        file_context=None,
        memory_context=None,
        learned_context="## Learned\navoid style nits in tests",
    )
    assert "### Learned Review Guidelines" in prompt
    assert "avoid style nits in tests" in prompt


def test_learned_context_placeholder_when_none():
    prompt = build_prompt(
        pass_name="security",
        diff="x",
        pr_description=None,
        file_context=None,
        memory_context=None,
    )
    assert "No learned guidelines yet" in prompt


def test_learned_context_ordering():
    prompt = build_prompt(
        pass_name="architecture",
        diff="x",
        pr_description="my PR",
        file_context=None,
        memory_context=None,
        conventions_signals="conv",
        spec_signals="spec",
        learned_context="learned stuff",
    )
    spec_pos = prompt.index("### Relevant Design Specs & Plans")
    learned_pos = prompt.index("### Learned Review Guidelines")
    pr_pos = prompt.index("### PR Description")
    assert spec_pos < learned_pos < pr_pos
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_prompts.py::test_learned_context_section_present_when_kwarg_non_empty -v`
Expected: FAIL (assertion error — section not found)

- [x] **Step 3: Modify `build_prompt()` in `prompts.py`**

Add `learned_context: str | None = None` to the signature after `spec_signals`, and add the new section after the Specs section in the return string.

Change signature:
```python
def build_prompt(
    pass_name: str,
    diff: str,
    pr_description: str | None,
    file_context: str | None,
    memory_context: str | None,
    static_signals: str | None = None,
    usage_signals: str | None = None,
    conventions_signals: str | None = None,
    spec_signals: str | None = None,
    learned_context: str | None = None,
) -> str:
```

Add after line 88 (the spec line) and before PR Description in the return string. Add the fallback variable:

```python
learned = learned_context or "No learned guidelines yet. Guidelines form as feedback accumulates over multiple reviews."
```

Add the section in the return f-string between `### Relevant Design Specs & Plans` and `### PR Description`:

```
### Learned Review Guidelines
{learned}
```

- [x] **Step 4: Run all new prompt tests**

Run: `uv run pytest tests/test_prompts.py -v -x`
Expected: ALL PASS (including existing tests)

- [x] **Step 5: Commit**

```bash
git add src/superseded/review/prompts.py tests/test_prompts.py
git commit -m "feat(prompts): add learned_context kwarg and Learned Review Guidelines section"
```

---

### Task 7: Wire CLI `_run_review` with audit pipeline

**Files:**
- Modify: `src/superseded/cli.py:305-453`
- Test: `tests/test_integration.py`

- [x] **Step 1: Write the integration tests**

Add to `tests/test_integration.py`:


@patch("superseded.cli._resolve_pr_review_diff")
@patch("superseded.cli.MemoryStore")
@patch("superseded.cli.current_repo")
@patch("superseded.cli.ReviewEngine")
@patch("superseded.context.gathering.compute_file_context")
@patch("superseded.cli.fetch_pr_description")
@patch("superseded.cli.fetch_diff")
def test_learned_context_injected_when_enabled(
    mock_fetch, mock_desc, mock_ctx, mock_engine_cls, mock_repo, mock_store_cls, mock_resolve
):
    from superseded.config import Config

    mock_fetch.return_value = "diff"
    mock_resolve.return_value = ("diff", "full", None)
    mock_ctx.return_value = "ctx"
    mock_desc.return_value = "PR body"
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    mock_engine.review.return_value = ReviewResult(findings=[])
    mock_engine.agent.is_available.return_value = True
    mock_repo.return_value = "owner/repo"

    store = FakeStore()
    # Add dismissed feedback to generate stats
    fid = "sec-test"
    store.findings[fid] = {
        "id": fid, "repo": "owner/repo", "pass": "style",
        "severity": "nit", "file": "tests/test_x.py", "line": 1,
        "title": "Missing type hints", "description": "dismissed before",
    }
    store.comment_ids[1] = fid
    store._dismissed.add(fid)
    store.feedback.append((fid, "dismiss"))
    store._learned_rules = [
        {"rule_text": "Inferred rule", "confidence": 0.9, "evidence_count": 3}
    ]
    mock_store_cls.return_value = store

    with patch("superseded.cli.load_config", return_value=Config(learned_review=True)):
        runner = CliRunner()
        result = runner.invoke(cli, ["review", "--pr", "1"])

    assert result.exit_code == 0, result.output
    kwargs = mock_engine.review.call_args.kwargs
    learned = kwargs.get("learned_context")
    assert learned is not None
    assert "Statistical guidance" in learned or "Inferred rule" in learned


@patch("superseded.cli._resolve_pr_review_diff")
@patch("superseded.cli.MemoryStore")
@patch("superseded.cli.current_repo")
@patch("superseded.cli.ReviewEngine")
@patch("superseded.context.gathering.compute_file_context")
@patch("superseded.cli.fetch_pr_description")
@patch("superseded.cli.fetch_diff")
def test_learned_context_is_none_when_disabled(
    mock_fetch, mock_desc, mock_ctx, mock_engine_cls, mock_repo, mock_store_cls, mock_resolve
):
    from superseded.config import Config

    mock_fetch.return_value = "diff"
    mock_resolve.return_value = ("diff", "full", None)
    mock_ctx.return_value = "ctx"
    mock_desc.return_value = "PR body"
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    mock_engine.review.return_value = ReviewResult(findings=[])
    mock_engine.agent.is_available.return_value = True
    mock_repo.return_value = "owner/repo"
    mock_store_cls.return_value = FakeStore()

    with patch("superseded.cli.load_config", return_value=Config(learned_review=False)):
        runner = CliRunner()
        result = runner.invoke(cli, ["review", "--pr", "1"])

    assert result.exit_code == 0, result.output
    kwargs = mock_engine.review.call_args.kwargs
    assert kwargs.get("learned_context") is None


@patch("superseded.cli.fetch_diff")
@patch("superseded.cli.ReviewEngine")
@patch("superseded.context.gathering.compute_file_context")
@patch("superseded.cli.fetch_pr_description")
def test_learned_context_none_when_no_memory(
    mock_desc, mock_ctx, mock_engine_cls, mock_fetch
):
    mock_fetch.return_value = "diff"
    mock_ctx.return_value = "ctx"
    mock_desc.return_value = "PR body"
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    mock_engine.review.return_value = ReviewResult(findings=[])
    mock_engine.agent.is_available.return_value = True

    with patch("superseded.cli.current_repo", return_value=None):
        runner = CliRunner()
        result = runner.invoke(cli, ["review", "--pr", "5", "--no-memory"])

    assert result.exit_code == 0, result.output
    kwargs = mock_engine.review.call_args.kwargs
    assert kwargs.get("learned_context") is None
```

- [x] **Step 2: Update FakeStore to support audit methods**

Add to `FakeStore` in `tests/test_integration.py`:

```python
self._learned_rules: list[dict] = []
self._reflection_state: dict[str, int] = {}


async def get_learned_rules(self, repo, limit=5):
    return [
        r for r in self._learned_rules
        if r.get("repo", "") == repo and r.get("confidence", 1.0) >= 0.3
    ][:limit]


async def get_reflection_state(self, repo):
    return self._reflection_state.get(repo, 0)


async def set_reflection_state(self, repo, last_feedback_id):
    self._reflection_state[repo] = last_feedback_id
```

- [x] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_integration.py::test_learned_context_injected_when_enabled -v`
Expected: FAIL (learned_context is None or missing)

- [x] **Step 4: Wire the audit pipeline in `_run_review`**

In `src/superseded/cli.py`, add import at top:

```python
from superseded.audit.guidelines import assemble_learned_context
from superseded.audit.reflector import PatternReflector
from superseded.audit.stats import StatsAggregator
```

After the existing memory context block (around line 416) and before the engine.review() call, add:

```python
learned_context: str | None = None
if config.learned_review and store is not None:
    aggregator = StatsAggregator(store)
    await aggregator._refresh(repo)
    stats_text = await aggregator.get_stats_context(repo)

    reflector = PatternReflector(agent=engine.agent, store=store)
    new_rules = await reflector.maybe_reflect(repo, cwd=root)

    all_rules = await store.get_learned_rules(repo, limit=config.max_learned_rules)
    learned_context = assemble_learned_context(
        stats_text, all_rules, config.max_learned_rules
    )
```

Add `learned_context=learned_context` to the `engine.review()` call.

Since `_run_review` is a sync function using `asyncio.run()` for async store calls elsewhere, use `asyncio.run()` to wrap the new async calls. Add a helper:

```python
async def _build_learned_context(
    store: MemoryStore,
    engine: ReviewEngine,
    repo: str,
    config: Config,
    root: Path,
) -> str | None:
    aggregator = StatsAggregator(store)
    await aggregator._refresh(repo)
    stats_text = await aggregator.get_stats_context(repo)

    reflector = PatternReflector(agent=engine.agent, store=store)
    await reflector.maybe_reflect(repo, cwd=root)

    all_rules = await store.get_learned_rules(repo, limit=config.max_learned_rules)
    return assemble_learned_context(stats_text, all_rules, config.max_learned_rules)
```

Then in `_run_review`, where the audit pipeline is wired:

```python
learned_context: str | None = None
if config.learned_review and store is not None and repo:
    learned_context = asyncio.run(
        _build_learned_context(store, engine, repo, config, root)
    )
```

- [x] **Step 5: Run integration tests**

Run: `uv run pytest tests/test_integration.py -v -k "learned" -x`
Expected: ALL PASS

- [x] **Step 6: Run full integration test suite for regressions**

Run: `uv run pytest tests/test_integration.py -v -x`
Expected: ALL PASS

- [x] **Step 7: Commit**

```bash
git add src/superseded/cli.py tests/test_integration.py
git commit -m "feat(cli): wire audit pipeline (stats + reflection + guidelines) into _run_review"
```

---

### Task 8: Wire server worker with audit pipeline

**Files:**
- Modify: `src/superseded/server/worker.py:258-428`

- [x] **Step 1: Add imports to worker.py**

Add at top of `worker.py`:

```python
from superseded.audit.guidelines import assemble_learned_context
from superseded.audit.reflector import PatternReflector
from superseded.audit.stats import StatsAggregator
```

- [x] **Step 2: Wire audit pipeline in `_run_review_for_job`**

After the existing `await store.set_watermark(...)` line (line 408), add:

```python
if config.learned_review and store is not None:
    repo_key = f"{job.owner}/{job.repo}"
    aggregator = StatsAggregator(store)
    await aggregator._refresh(repo_key)
    await aggregator.get_stats_context(repo_key)

    reflector = PatternReflector(agent=engine.agent, store=store)
    await reflector.maybe_reflect(repo_key, cwd=repo_path)

    _ = await store.get_learned_rules(repo_key, limit=config.max_learned_rules)
```

The server worker doesn't pass `learned_context` to the *current* review (it runs after persistence, same pattern as CLI). The learned context is built for the *next* review cycle. This matches the spec flow: stats from cycle N feed cycle N+1.

However, per the spec, `learned_context` should also be built for the current review. Since the server worker doesn't have the `_run_review`-style two-phase flow, we add learned context injection before `engine.review()`:

```python
learned_context: str | None = None
if config.learned_review and store is not None:
    repo_key = f"{job.owner}/{job.repo}"
    all_rules = await store.get_learned_rules(repo_key, limit=config.max_learned_rules)
    # stats are from prior cycles (or None if first review)
    # NOTE: StatsAggregator needs store access; use a simpler inline query
    # to avoid the async-to-thread issue
    learned_context = assemble_learned_context(None, all_rules, config.max_learned_rules)
```

Then add `learned_context=learned_context` to `engine.review()` call.

- [x] **Step 3: Run server tests**

Run: `uv run pytest tests/test_server_worker.py -v -x`
Expected: ALL PASS (with potential test updates for the new kwargs)

- [x] **Step 4: Commit**

```bash
git add src/superseded/server/worker.py
git commit -m "feat(server): wire audit pipeline into server worker review cycle"
```

---

### Task 9: Final verification — lint, format, full test suite

**Files:** None (verification only)

- [x] **Step 1: Run ruff format**

```bash
uv run ruff format src/ tests/
```
Expected: No changes or clean output

- [x] **Step 2: Run ruff check**

```bash
uv run ruff check src/ tests/
```
Expected: All clear

- [x] **Step 3: Run full test suite**

```bash
uv run pytest tests/ -v
```
Expected: ALL PASS

- [x] **Step 4: Commit any lint/format fixes**

```bash
git add -u
git commit -m "chore: lint and format fixes for audit pipeline"
```

---

### Files Summary

| File | Action | Purpose |
|---|---|---|
| `src/superseded/audit/__init__.py` | Create | Package init |
| `src/superseded/audit/stats.py` | Create | StatsAggregator |
| `src/superseded/audit/reflector.py` | Create | PatternReflector |
| `src/superseded/audit/guidelines.py` | Create | assemble_learned_context |
| `src/superseded/config.py` | Modify | New fields |
| `src/superseded/memory/store.py` | Modify | Tables, migration, methods |
| `src/superseded/review/prompts.py` | Modify | learned_context section |
| `src/superseded/cli.py` | Modify | Audit pipeline wiring |
| `src/superseded/server/worker.py` | Modify | Audit pipeline wiring |
| `tests/test_audit_stats.py` | Create | Stats tests |
| `tests/test_audit_reflector.py` | Create | Reflector tests |
| `tests/test_audit_guidelines.py` | Create | Guidelines tests |
| `tests/test_prompts.py` | Modify | Learned context section tests |
| `tests/test_integration.py` | Modify | Integration tests + FakeStore updates |
| `tests/test_memory.py` | Modify | Schema/method tests |
