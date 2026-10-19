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


# --- StatsAggregator.get_report (Task 7) ---


async def test_get_report_zero_data():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(tmpdir)
        await store.init()
        agg = StatsAggregator(store)
        report = await agg.get_report("owner/repo")
        assert report["total"] == 0
        assert report["dismissed"] == 0
        assert report["dismiss_rate"] == 0.0
        assert report["verifier_dropped"] == 0
        assert report["verifier_drop_rate"] == 0.0
        assert report["open_pr_findings"] == 0
        assert report["max_review_round"] == 0
        assert report["by_pass"] == []


async def test_get_report_full():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(tmpdir)
        await store.init()

        # 4 findings: 2 human dismissals, 1 verifier drop, 1 accepted.
        await store.record_findings_batch(
            [
                {
                    "id": "f1",
                    "pass_name": "security",
                    "severity": "critical",
                    "file": "a.py",
                    "line": 1,
                    "title": "t1",
                    "description": "d",
                    "reasoning": "",
                },
                {
                    "id": "f2",
                    "pass_name": "security",
                    "severity": "critical",
                    "file": "b.py",
                    "line": 2,
                    "title": "t2",
                    "description": "d",
                    "reasoning": "",
                },
                {
                    "id": "f3",
                    "pass_name": "style",
                    "severity": "nit",
                    "file": "c.py",
                    "line": 3,
                    "title": "t3",
                    "description": "d",
                    "reasoning": "",
                    "verification": "dropped",
                    "verification_reason": "fp",
                },
                {
                    "id": "f4",
                    "pass_name": "style",
                    "severity": "nit",
                    "file": "d.py",
                    "line": 4,
                    "title": "t4",
                    "description": "d",
                    "reasoning": "",
                },
            ],
            "owner/repo",
        )
        await store.record_feedback("f1", "dismiss")
        await store.record_feedback("f2", "dismiss")
        await store.record_verification_feedback("f3")
        await store.record_feedback("f4", "helpful")

        await store.record_pr_findings_batch(
            "owner/repo",
            7,
            [
                {
                    "fingerprint": "fp1",
                    "finding_id": "f1",
                    "pass_name": "security",
                    "severity": "critical",
                    "file": "a.py",
                    "line": 1,
                    "title": "t1",
                    "status": "open",
                }
            ],
        )
        await store.set_review_round("owner/repo", 7, 3)

        agg = StatsAggregator(store)
        report = await agg.get_report("owner/repo")

        assert report["total"] == 4
        assert report["dismissed"] == 2
        assert report["dismiss_rate"] == 0.5
        assert report["verifier_dropped"] == 1
        assert report["verifier_drop_rate"] == 0.25
        assert report["open_pr_findings"] == 1
        assert report["max_review_round"] == 3

        by_key = {(r["pass"], r["severity"]): r for r in report["by_pass"]}
        assert ("security", "critical") in by_key
        assert ("style", "nit") in by_key
        sec = by_key[("security", "critical")]
        assert sec["total"] == 2 and sec["dismissed"] == 2 and sec["accepted"] == 0
        assert sec["dismiss_rate"] == 1.0


async def test_refresh_excludes_verifier_drops_from_dismissed():
    """Verifier drops must not inflate the human ``dismissed`` count in review_stats."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(tmpdir)
        await store.init()

        # Kept finding with no feedback.
        await store.record_finding(
            "keep", "owner/repo", "security", "critical", "a.py", 1, "kept", "d"
        )
        # Human-dismissed finding.
        await store.record_finding(
            "human", "owner/repo", "security", "critical", "b.py", 2, "human", "d"
        )
        await store.record_feedback("human", "dismiss")
        # Verifier-dropped finding.
        await store.record_findings_batch(
            [
                {
                    "id": "drop",
                    "pass_name": "security",
                    "severity": "critical",
                    "file": "c.py",
                    "line": 3,
                    "title": "drop",
                    "description": "d",
                    "reasoning": "",
                    "verification": "dropped",
                    "verification_reason": "fp",
                }
            ],
            "owner/repo",
        )
        await store.record_verification_feedback("drop")

        agg = StatsAggregator(store)
        report = await agg.get_report("owner/repo")

        rows = await store.get_review_stats("owner/repo", 1)
        assert len(rows) == 1
        assert rows[0]["dismissed"] == 1  # verifier drop excluded, human only

        assert report["total"] == 3
        assert report["dismissed"] == 1
        assert report["verifier_dropped"] == 1


async def _test_refresh_excludes_verifier_only():
    """A verifier-only finding must not create a review_stats row."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(tmpdir)
        await store.init()

        # Verifier-only finding: feedback row with source='verifier'.
        await store.record_finding(
            "drop", "owner/repo", "security", "critical", "c.py", 3, "drop", "d"
        )
        await store.record_verification_feedback("drop")

        agg = StatsAggregator(store)
        await agg._refresh("owner/repo")

        # Query review_stats directly: get_review_stats() filters on total >= 1,
        # so it would hide a lingering zero-total row if the HAVING clause were
        # removed. A verifier-only group must not insert any row at all.
        async with store._db() as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM review_stats WHERE repo = ?", ("owner/repo",)
            )
            row = await cursor.fetchone()
        assert row[0] == 0

        # A human-dismissed finding produces exactly one row with total=1.
        await store.record_finding(
            "human", "owner/repo", "security", "critical", "b.py", 2, "human", "d"
        )
        await store.record_feedback("human", "dismiss")
        await agg._refresh("owner/repo")

        rows = await store.get_review_stats("owner/repo", 1)
        assert len(rows) == 1
        assert rows[0]["total"] == 1
        assert rows[0]["dismissed"] == 1
        assert rows[0]["accepted"] == 0


def test_refresh_excludes_verifier_only():
    asyncio.run(_test_refresh_excludes_verifier_only())
