from __future__ import annotations

import asyncio
import json

import pytest

from superseded.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test.db"
    s = MemoryStore(db_path=db_path)
    asyncio.run(s.init())
    return s


def test_installations_table_exists(store):
    async def _check():
        import aiosqlite

        async with aiosqlite.connect(store.db_path) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='installations'"
            )
            return await cursor.fetchone() is not None

    assert asyncio.run(_check())


def test_record_installation(store):
    asyncio.run(
        store.record_installation(
            installation_id=12345,
            owner="octocat",
            repos=["hello-world", "mona-lisa"],
        )
    )

    async def _get():
        import aiosqlite

        async with aiosqlite.connect(store.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM installations WHERE app_installation_id = 12345"
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    result = asyncio.run(_get())
    assert result is not None
    assert result["owner"] == "octocat"
    repos = json.loads(result["repos"])
    assert "hello-world" in repos
    assert "mona-lisa" in repos


def test_get_installation(store):
    asyncio.run(
        store.record_installation(
            installation_id=99999,
            owner="test-org",
            repos=["repo-a"],
        )
    )
    result = asyncio.run(store.get_installation(99999))
    assert result is not None
    assert result["owner"] == "test-org"


def test_get_installation_not_found(store):
    result = asyncio.run(store.get_installation(0))
    assert result is None


def test_remove_installation(store):
    asyncio.run(
        store.record_installation(
            installation_id=11111,
            owner="doomed",
            repos=["temp-repo"],
        )
    )
    asyncio.run(store.remove_installation(11111))
    result = asyncio.run(store.get_installation(11111))
    assert result is None
