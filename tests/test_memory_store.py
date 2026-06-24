from __future__ import annotations

import asyncio
from pathlib import Path

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


def test_migration_adds_reasoning_column(tmp_path):
    import aiosqlite

    db_path = tmp_path / "old.db"
    asyncio.run(_init_old_db(db_path))

    store = MemoryStore(db_path=db_path)
    asyncio.run(store.init())

    async def _check():
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("PRAGMA table_info(findings)")
            cols = {row[1] for row in await cursor.fetchall()}
            return "reasoning" in cols

    assert asyncio.run(_check())


async def _init_old_db(db_path: Path) -> None:
    import aiosqlite

    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS findings (
                id TEXT PRIMARY KEY,
                repo TEXT,
                pass TEXT,
                severity TEXT,
                file TEXT,
                line INTEGER,
                title TEXT,
                description TEXT,
                dismissed BOOLEAN DEFAULT FALSE,
                comment_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                finding_id TEXT REFERENCES findings(id),
                action TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)


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
