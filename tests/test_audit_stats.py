from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import aiosqlite

from superseded.audit.stats import StatsAggregator
from superseded.memory.store import MemoryStore


def _make_store(tmpdir: str) -> MemoryStore:
    return MemoryStore(Path(tmpdir) / "test.db")


async def _seed_findings_and_feedback(store: MemoryStore) -> None:
    """Insert two findings and feedback for them."""
    await store.record_finding(
        finding_id="sec-1",
        repo="owner/repo",
        pass_name="security",
        severity="critical",
        file="src/auth.py",
        line=10,
        title="SQL injection",
        description="desc",
    )
    await store.record_finding(
        finding_id="sec-2",
        repo="owner/repo",
        pass_name="security",
        severity="critical",
        file="src/login.py",
        line=20,
        title="Weak hash",
        description="desc",
    )
    for fid in ("sec-1", "sec-2"):
        await store.record_feedback(fid, "dismiss")


async def _test_refresh_creates_rows():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(tmpdir)
        await store.init()
        await _seed_findings_and_feedback(store)

        agg = StatsAggregator(store)
        await agg._refresh("owner/repo")

        async with store._db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM review_stats WHERE repo = ?", ("owner/repo",))
            rows = await cursor.fetchall()
        assert len(rows) == 1
        row = dict(rows[0])
        assert row["pass"] == "security"
        assert row["severity"] == "critical"
        assert row["total"] == 2
        assert row["dismissed"] == 2
        assert row["accepted"] == 0


def test_refresh_creates_rows():
    asyncio.run(_test_refresh_creates_rows())


async def _test_get_stats_context_none():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(tmpdir)
        await store.init()
        agg = StatsAggregator(store)
        result = await agg.get_stats_context("owner/repo")
        assert result is None


def test_get_stats_context_none():
    asyncio.run(_test_get_stats_context_none())


async def _test_get_stats_context_high_dismissal():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(tmpdir)
        await store.init()

        for i in range(6):
            fid = f"sec-{i}"
            await store.record_finding(
                finding_id=fid,
                repo="owner/repo",
                pass_name="style",
                severity="warning",
                file="tests/test_auth.py",
                line=i,
                title=f"Style issue {i}",
                description="desc",
            )
            await store.record_feedback(fid, "dismiss")

        agg = StatsAggregator(store)
        await agg._refresh("owner/repo")
        result = await agg.get_stats_context("owner/repo")

        assert result is not None
        assert "test" in result
        assert "style" in result
        assert "dismissal rate 100%" in result
        assert "Suppress" in result


def test_get_stats_context_high_dismissal():
    asyncio.run(_test_get_stats_context_high_dismissal())


async def _test_get_stats_context_high_acceptance():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(tmpdir)
        await store.init()

        for i in range(6):
            fid = f"sec-{i}"
            await store.record_finding(
                finding_id=fid,
                repo="owner/repo",
                pass_name="security",
                severity="critical",
                file="src/auth.py",
                line=i,
                title=f"Vuln {i}",
                description="desc",
            )
            await store.record_feedback(fid, "helpful")

        agg = StatsAggregator(store)
        await agg._refresh("owner/repo")
        result = await agg.get_stats_context("owner/repo")

        assert result is not None
        assert "security" in result
        assert "acceptance rate 100%" in result
        assert "Continue" in result


def test_get_stats_context_high_acceptance():
    asyncio.run(_test_get_stats_context_high_acceptance())


async def _test_refresh_upserts():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(tmpdir)
        await store.init()

        await store.record_finding(
            finding_id="sec-1",
            repo="owner/repo",
            pass_name="security",
            severity="critical",
            file="src/auth.py",
            line=10,
            title="SQL injection",
            description="desc",
        )
        await store.record_feedback("sec-1", "dismiss")

        agg = StatsAggregator(store)
        await agg._refresh("owner/repo")

        async with store._db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT total FROM review_stats WHERE repo = ?", ("owner/repo",)
            )
            row = await cursor.fetchone()
        assert dict(row)["total"] == 1

        await store.record_finding(
            finding_id="sec-2",
            repo="owner/repo",
            pass_name="security",
            severity="critical",
            file="src/login.py",
            line=20,
            title="Weak hash",
            description="desc",
        )
        await store.record_feedback("sec-2", "helpful")
        await agg._refresh("owner/repo")

        async with store._db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT total, accepted, dismissed FROM review_stats WHERE repo = ?",
                ("owner/repo",),
            )
            row = await cursor.fetchone()
        r = dict(row)
        assert r["total"] == 2
        assert r["accepted"] == 1
        assert r["dismissed"] == 1


def test_refresh_upserts():
    asyncio.run(_test_refresh_upserts())


async def _test_refresh_excludes_without_feedback():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(tmpdir)
        await store.init()

        await store.record_finding(
            finding_id="sec-1",
            repo="owner/repo",
            pass_name="security",
            severity="critical",
            file="src/auth.py",
            line=10,
            title="SQL injection",
            description="desc",
        )
        await store.record_finding(
            finding_id="sec-2",
            repo="owner/repo",
            pass_name="security",
            severity="critical",
            file="src/login.py",
            line=20,
            title="Weak hash",
            description="desc",
        )
        await store.record_feedback("sec-1", "dismiss")

        agg = StatsAggregator(store)
        await agg._refresh("owner/repo")

        async with store._db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT total FROM review_stats WHERE repo = ?", ("owner/repo",)
            )
            row = await cursor.fetchone()
        assert dict(row)["total"] == 1


def test_refresh_excludes_without_feedback():
    asyncio.run(_test_refresh_excludes_without_feedback())


async def _test_get_stats_context_repo_isolation():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(tmpdir)
        await store.init()

        for i in range(6):
            fid = f"sec-{i}"
            await store.record_finding(
                finding_id=fid,
                repo="owner/repo-a",
                pass_name="security",
                severity="critical",
                file="src/auth.py",
                line=i,
                title=f"Vuln {i}",
                description="desc",
            )
            await store.record_feedback(fid, "helpful")

        agg = StatsAggregator(store)
        await agg._refresh("owner/repo-a")

        result = await agg.get_stats_context("owner/repo-b")
        assert result is None


def test_get_stats_context_repo_isolation():
    asyncio.run(_test_get_stats_context_repo_isolation())


async def test_memory_store_refresh_review_stats(tmp_path):
    from superseded.memory.store import MemoryStore

    store = MemoryStore(Path(tmp_path) / "st.db")
    await store.open()
    try:
        await store.record_finding("f1", "octo/r", "security", "critical", "a.py", 1, "t", "d")
        await store.record_finding(
            "f2", "octo/r", "security", "critical", "tests/b.py", 2, "t", "d"
        )
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
