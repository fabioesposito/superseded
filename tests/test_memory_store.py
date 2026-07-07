from __future__ import annotations

import asyncio

import pytest

from superseded.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test.db"
    s = MemoryStore(db_path=db_path)
    asyncio.run(s.init())
    return s


def test_reasoning_column_exists(store):
    async def _check():
        import aiosqlite

        async with aiosqlite.connect(store.db_path) as db:
            cursor = await db.execute("PRAGMA table_info(findings)")
            cols = {row[1] for row in await cursor.fetchall()}
            return "reasoning" in cols

    assert asyncio.run(_check())


def test_reasoning_roundtrip(store):
    asyncio.run(
        store.record_finding(
            finding_id="test-1",
            repo="owner/repo",
            pass_name="security",
            severity="critical",
            file="a.py",
            line=1,
            title="bad",
            description="desc",
            reasoning="because X",
        )
    )

    async def _get():
        import aiosqlite

        async with aiosqlite.connect(store.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT reasoning FROM findings WHERE id = 'test-1'")
            row = await cursor.fetchone()
            return dict(row) if row else None

    result = asyncio.run(_get())
    assert result is not None
    assert result["reasoning"] == "because X"


def test_reasoning_empty_by_default(store):
    asyncio.run(
        store.record_finding(
            finding_id="test-2",
            repo="owner/repo",
            pass_name="style",
            severity="nit",
            file="b.py",
            line=5,
            title="naming",
            description="desc",
        )
    )

    async def _get():
        import aiosqlite

        async with aiosqlite.connect(store.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT reasoning FROM findings WHERE id = 'test-2'")
            row = await cursor.fetchone()
            return dict(row) if row else None

    result = asyncio.run(_get())
    assert result is not None
    assert result["reasoning"] == ""


def test_record_finding_upserts_on_change(store):
    asyncio.run(
        store.record_finding(
            finding_id="f1",
            repo="owner/repo",
            pass_name="security",
            severity="critical",
            file="a.py",
            line=1,
            title="title",
            description="desc1",
            reasoning="reason1",
        )
    )
    asyncio.run(
        store.record_finding(
            finding_id="f1",
            repo="owner/repo",
            pass_name="security",
            severity="important",
            file="a.py",
            line=1,
            title="title",
            description="desc2",
            reasoning="reason2",
        )
    )

    async def _get():
        import aiosqlite

        async with aiosqlite.connect(store.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM findings WHERE id = 'f1'")
            return dict(await cursor.fetchone())

    row = asyncio.run(_get())
    assert row["severity"] == "important"
    assert row["description"] == "desc2"
    assert row["reasoning"] == "reason2"


def test_record_finding_preserves_comment_id_on_upsert(store):
    asyncio.run(
        store.record_finding(
            finding_id="f2",
            repo="owner/repo",
            pass_name="security",
            severity="critical",
            file="a.py",
            line=1,
            title="title",
            description="desc1",
        )
    )
    asyncio.run(store.set_comment_id("f2", 42))
    asyncio.run(
        store.record_finding(
            finding_id="f2",
            repo="owner/repo",
            pass_name="security",
            severity="warning",
            file="a.py",
            line=1,
            title="title",
            description="desc2",
        )
    )

    async def _get():
        import aiosqlite

        async with aiosqlite.connect(store.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM findings WHERE id = 'f2'")
            return dict(await cursor.fetchone())

    row = asyncio.run(_get())
    assert row["severity"] == "warning"
    assert row["description"] == "desc2"
    assert row["comment_id"] == 42


def test_dismissed_findings_include_reasoning(store):
    asyncio.run(
        store.record_finding(
            finding_id="test-3",
            repo="owner/repo",
            pass_name="performance",
            severity="suggestion",
            file="c.py",
            line=10,
            title="slow",
            description="desc",
            reasoning="N+1 query",
        )
    )
    asyncio.run(store.record_feedback("test-3", "dismiss"))
    dismissed = asyncio.run(store.get_dismissed_findings("owner/repo"))
    assert len(dismissed) == 1
    assert dismissed[0]["reasoning"] == "N+1 query"


async def test_open_reuses_single_connection(tmp_path):
    """While a long-lived connection is open, query methods reuse it (no new connects)."""
    import superseded.memory.store as store_mod

    store = MemoryStore(db_path=tmp_path / "reuse.db")
    await store.open()  # alembic upgrade happens here, before counting begins

    connects = {"n": 0}
    real_connect = store_mod.aiosqlite.connect

    def counting_connect(*args, **kwargs):
        connects["n"] += 1
        return real_connect(*args, **kwargs)

    store_mod.aiosqlite.connect = counting_connect
    try:
        await store.record_finding(
            finding_id="a",
            repo="o/r",
            pass_name="security",
            severity="critical",
            file="x.py",
            line=1,
            title="t",
            description="d",
        )
        await store.record_finding(
            finding_id="b",
            repo="o/r",
            pass_name="security",
            severity="critical",
            file="x.py",
            line=2,
            title="t2",
            description="d2",
        )
        await store.record_feedback("a", "dismiss")
        await store.get_dismissed_findings("o/r")
    finally:
        store_mod.aiosqlite.connect = real_connect
    assert connects["n"] == 0  # no new connects while the long-lived conn is open
    await store.close()


async def test_aenter_aexit_round_trip(tmp_path):
    store = MemoryStore(db_path=tmp_path / "ctx.db")
    async with store:
        assert store._conn is not None
    assert store._conn is None


async def test_get_watermark_returns_none_when_absent(tmp_path):
    store = MemoryStore(db_path=tmp_path / "m.db")
    await store.init()
    assert await store.get_watermark("owner/repo", 7) is None


async def test_set_then_get_watermark(tmp_path):
    store = MemoryStore(db_path=tmp_path / "m.db")
    await store.init()
    await store.set_watermark("owner/repo", 7, "abc123")
    assert await store.get_watermark("owner/repo", 7) == "abc123"


async def test_set_watermark_replaces_existing(tmp_path):
    store = MemoryStore(db_path=tmp_path / "m.db")
    await store.init()
    await store.set_watermark("owner/repo", 7, "abc123")
    await store.set_watermark("owner/repo", 7, "def456")
    assert await store.get_watermark("owner/repo", 7) == "def456"


async def test_watermark_keys_per_repo_and_pr(tmp_path):
    store = MemoryStore(db_path=tmp_path / "m.db")
    await store.init()
    await store.set_watermark("owner/repo", 7, "aaa")
    await store.set_watermark("owner/repo", 8, "bbb")
    await store.set_watermark("other/repo", 7, "ccc")
    assert await store.get_watermark("owner/repo", 7) == "aaa"
    assert await store.get_watermark("owner/repo", 8) == "bbb"
    assert await store.get_watermark("other/repo", 7) == "ccc"
