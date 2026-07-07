from __future__ import annotations

import asyncio
import sqlite3

import pytest

from superseded.memory import store as store_mod
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


async def test_connection_has_busy_timeout(tmp_path):
    """Open() must configure a non-zero busy_timeout so concurrent writers wait
    instead of hard-failing with 'database is locked'."""
    store = MemoryStore(db_path=tmp_path / "bt.db")
    await store.open()
    cursor = await store._conn.execute("PRAGMA busy_timeout")
    (val,) = await cursor.fetchone()
    assert val == store_mod._BUSY_TIMEOUT_MS
    await store.close()


async def test_concurrent_writes_wait_instead_of_locking(tmp_path):
    """Regression for the 'database is locked after abort/error' symptom.

    One connection holds an open write transaction; a second connection writing
    to the same DB must block-and-retry (busy_timeout) rather than raising
    'database is locked' immediately. With busy_timeout=0 (the pre-fix default)
    the second write raises before the holder commits, failing this test.
    """
    db = tmp_path / "lock.db"
    holder = MemoryStore(db_path=db)
    waiter = MemoryStore(db_path=db)
    await holder.open()
    await waiter.open()

    await holder._conn.execute("BEGIN IMMEDIATE")
    await holder._conn.execute(
        "INSERT INTO findings (id, repo, pass, severity, file, line, title, description) "
        "VALUES ('hold', 'o/r', 'security', 'critical', 'x', 1, 't', 'd')"
    )

    async def _waiter_write():
        await waiter.record_finding(
            finding_id="w1",
            repo="o/r",
            pass_name="security",
            severity="critical",
            file="y.py",
            line=2,
            title="t2",
            description="d2",
        )

    waiter_task = asyncio.create_task(_waiter_write())
    await asyncio.sleep(0.3)
    await holder._conn.execute("COMMIT")

    await asyncio.wait_for(waiter_task, timeout=5.0)

    await holder.close()
    await waiter.close()


async def test_retry_locked_succeeds_after_transient_locks(monkeypatch):
    """The retry loop re-invokes the callable when a transient lock error occurs
    and completes once it stops raising."""
    monkeypatch.setattr(store_mod, "_LOCK_RETRY_BASE_DELAY", 0.0)

    attempts = {"n": 0}

    async def _op() -> None:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise sqlite3.OperationalError("database is locked")

    await store_mod._retry_locked(_op)
    assert attempts["n"] == 3


async def test_retry_locked_gives_up_after_max(monkeypatch):
    """After _LOCK_RETRY_MAX attempts the original error is re-raised."""
    monkeypatch.setattr(store_mod, "_LOCK_RETRY_BASE_DELAY", 0.0)

    attempts = {"n": 0}

    async def _op() -> None:
        attempts["n"] += 1
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        await store_mod._retry_locked(_op)
    assert attempts["n"] == store_mod._LOCK_RETRY_MAX


async def test_retry_locked_does_not_retry_non_lock_errors(monkeypatch):
    """A non-'locked' OperationalError must surface immediately (no retry)."""
    monkeypatch.setattr(store_mod, "_LOCK_RETRY_BASE_DELAY", 0.0)

    attempts = {"n": 0}

    async def _op() -> None:
        attempts["n"] += 1
        raise sqlite3.OperationalError("no such table: widgets")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        await store_mod._retry_locked(_op)
    assert attempts["n"] == 1


async def test_close_does_not_persist_uncommitted_writes(tmp_path):
    """close() rolls back any open transaction so an aborted/interrupted write
    never leaks into the next caller (regression for the shared-conn server path
    and for CLI runs interrupted mid-persist)."""
    db = tmp_path / "rb.db"
    store = MemoryStore(db_path=db)
    await store.open()
    await store._conn.execute("BEGIN IMMEDIATE")
    await store._conn.execute(
        "INSERT INTO findings (id, repo, pass, severity, file, line, title, description) "
        "VALUES ('leak', 'o/r', 'security', 'critical', 'f', 1, 't', 'd')"
    )
    await store.close()  # no commit

    reopened = MemoryStore(db_path=db)
    await reopened.open()
    cursor = await reopened._conn.execute("SELECT COUNT(*) FROM findings WHERE id = 'leak'")
    (count,) = await cursor.fetchone()
    await reopened.close()
    assert count == 0


async def test_record_finding_retries_past_a_lock(tmp_path, monkeypatch):
    """End-to-end: record_finding survives an execute() that raises 'locked'
    a few times, then commits. Confirms the retry wiring on the hot path."""
    monkeypatch.setattr(store_mod, "_LOCK_RETRY_BASE_DELAY", 0.0)

    store = MemoryStore(db_path=tmp_path / "retry.db")
    await store.open()

    real_execute = store._conn.execute
    state = {"n": 0}

    async def flaky_execute(sql, *params):
        # Only inject the lock error into the actual finding INSERT, not the
        # pragma queries that open() already ran before this patch.
        if "INSERT INTO findings" in sql and state["n"] < 2:
            state["n"] += 1
            raise sqlite3.OperationalError("database is locked")
        return await real_execute(sql, *params)

    # aiosqlite proxy: assigning on the instance shadows the underlying method.
    store._conn.execute = flaky_execute  # type: ignore[method-assign]
    try:
        await store.record_finding(
            finding_id="rf1",
            repo="o/r",
            pass_name="security",
            severity="critical",
            file="z.py",
            line=9,
            title="t",
            description="d",
        )
    finally:
        store._conn.execute = real_execute  # type: ignore[method-assign]
        await store.close()

    assert state["n"] == 2  # it raised twice then succeeded
