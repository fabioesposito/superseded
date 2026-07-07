from __future__ import annotations

import os

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


async def test_postgres_open_adopts_pre_alembic_db():
    """A DB with the legacy schema (no alembic_version) gets stamped + upgraded,
    and existing data survives."""
    import asyncpg

    from superseded.memory.postgres import PostgresStore

    # Wipe to a clean slate, then build a partial legacy schema (no alembic_version).
    conn = await asyncpg.connect(_DSN)
    try:
        for table in (
            "installation_config",
            "feedback",
            "findings",
            "installations",
            "review_watermarks",
            "review_stats",
            "learned_rules",
            "reflection_state",
            "alembic_version",
        ):
            await conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        await conn.execute("CREATE TABLE findings (id TEXT PRIMARY KEY, repo TEXT, pass TEXT)")
        await conn.execute("INSERT INTO findings (id, repo, pass) VALUES ('x', 'o/r', 'security')")
    finally:
        await conn.close()

    store = PostgresStore(_DSN, max_size=4)
    await store.open()

    # Seeded finding must survive adoption.
    async with store._pool.acquire() as c:
        count = await c.fetchval("SELECT COUNT(*) FROM findings")

    await store.close()
    assert count == 1
