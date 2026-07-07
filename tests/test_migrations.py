from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from superseded.memory import alembic_runner
from superseded.memory.store import MemoryStore


def _script_location() -> str:
    import importlib.resources

    return str(importlib.resources.files("superseded.memory.migrations"))


def _make_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", _script_location())
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_head_revision_is_0002():
    cfg = _make_config("sqlite+aiosqlite:///:memory:")
    script_dir = ScriptDirectory.from_config(cfg)
    assert script_dir.get_current_head() == "0002"


def _sqlite_url_for(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path}"


def test_normalize_url_passes_sqlite_through():
    assert (
        alembic_runner.normalize_url("sqlite+aiosqlite:///tmp/x.db")
        == "sqlite+aiosqlite:///tmp/x.db"
    )


def test_normalize_url_postgres_to_asyncpg():
    assert (
        alembic_runner.normalize_url("postgres://u:p@host/db") == "postgresql+asyncpg://u:p@host/db"
    )


def test_normalize_url_postgresql_to_asyncpg():
    assert (
        alembic_runner.normalize_url("postgresql://u:p@host/db")
        == "postgresql+asyncpg://u:p@host/db"
    )


def test_upgrade_fresh_db_creates_schema(tmp_path):
    db = tmp_path / "fresh.db"

    rev = alembic_runner.upgrade(_sqlite_url_for(db))

    assert rev == "0002"

    async def has_table(name: str) -> bool:
        async with aiosqlite.connect(db) as conn:
            cur = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
            )
            return await cur.fetchone() is not None

    assert asyncio.run(has_table("findings"))
    assert asyncio.run(has_table("alembic_version"))
    assert asyncio.run(has_table("installation_config"))


def test_upgrade_pre_alembic_db_stamps_and_preserves_data(tmp_path):
    db = tmp_path / "legacy.db"

    asyncio.run(_seed_legacy_db(db))

    rev = alembic_runner.upgrade(_sqlite_url_for(db))

    assert rev == "0002"

    async def count_findings() -> int:
        async with aiosqlite.connect(db) as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM findings")
            row = await cur.fetchone()
            return int(row[0])

    assert asyncio.run(count_findings()) == 1


async def _seed_legacy_db(db: Path) -> None:
    async with aiosqlite.connect(db) as conn:
        await conn.executescript(
            """
            CREATE TABLE findings (
                id TEXT PRIMARY KEY, repo TEXT, pass TEXT, severity TEXT,
                file TEXT, line INTEGER, reasoning TEXT DEFAULT '',
                title TEXT, description TEXT, dismissed BOOLEAN DEFAULT FALSE,
                comment_id INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await conn.execute(
            "INSERT INTO findings (id, repo, pass, severity, file, line, title, description) "
            "VALUES ('abc', 'o/r', 'security', 'high', 'f.py', 1, 't', 'd')"
        )
        await conn.commit()


def test_upgrade_is_idempotent(tmp_path):
    db = tmp_path / "idem.db"
    url = _sqlite_url_for(db)

    first = alembic_runner.upgrade(url)
    second = alembic_runner.upgrade(url)

    assert first == second == "0002"


def test_migration_0002_is_noop_on_sqlite(tmp_path):
    db = tmp_path / "twostep.db"
    alembic_runner.upgrade(_sqlite_url_for(db))

    # Upgrading again (already at head 0002) must be a clean no-op.
    assert alembic_runner.upgrade(_sqlite_url_for(db)) == "0002"


def test_memory_store_open_adopts_pre_alembic_db(tmp_path):
    db = tmp_path / "store_legacy.db"

    # Seed a legacy DB (old schema, no alembic_version) with one finding.
    asyncio.run(_seed_legacy_db(db))

    store = MemoryStore(db_path=db)
    asyncio.run(store.open())

    # Data survived, and the store still works for reads via its own connection.
    async def _roundtrip() -> int:
        async with store._db() as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM findings")
            return int((await cur.fetchone())[0])

    assert asyncio.run(_roundtrip()) == 1
    asyncio.run(store.close())


def test_normalize_url_sqlite_to_aiosqlite():
    assert alembic_runner.normalize_url("sqlite:///tmp/x.db") == "sqlite+aiosqlite:///tmp/x.db"


def test_normalize_url_sqlite_default_scheme():
    assert alembic_runner.normalize_url("sqlite://") == "sqlite+aiosqlite://"


def test_probe_propagates_original_error_not_dispose_error(monkeypatch):
    """If both the probe op and engine.dispose() raise, the probe error wins.

    Regression: previously dispose() in ``finally`` masked the real error,
    producing a confusing double-trace (seen when greenlet was missing).
    """
    connect_err = RuntimeError("CONNECT_FAILED")
    dispose_err = RuntimeError("DISPOSE_FAILED")

    class _ConnCtx:
        async def __aenter__(self):
            raise connect_err

        async def __aexit__(self, *_):
            return False

    class _BadEngine:
        def connect(self):
            return _ConnCtx()

        async def dispose(self):
            raise dispose_err

    monkeypatch.setattr(alembic_runner, "create_async_engine", lambda _url: _BadEngine())

    with pytest.raises(RuntimeError, match="CONNECT_FAILED"):
        asyncio.run(alembic_runner._probe("sqlite+aiosqlite:///x"))


def test_current_revision_propagates_original_error_not_dispose_error(monkeypatch):
    """Same regression guard as above, for _current_revision's inner _get."""
    connect_err = RuntimeError("CONNECT_FAILED")
    dispose_err = RuntimeError("DISPOSE_FAILED")

    class _ConnCtx:
        async def __aenter__(self):
            raise connect_err

        async def __aexit__(self, *_):
            return False

    class _BadEngine:
        def connect(self):
            return _ConnCtx()

        async def dispose(self):
            raise dispose_err

    monkeypatch.setattr(alembic_runner, "create_async_engine", lambda _url: _BadEngine())

    with pytest.raises(RuntimeError, match="CONNECT_FAILED"):
        alembic_runner._current_revision("sqlite+aiosqlite:///x")


def test_models_match_head_revision_no_drift(tmp_path):
    """Guardrail: the SQLAlchemy models must match a DB upgraded to head.

    Fails if someone edits the models without regenerating a migration
    (equivalent to ``alembic check``). This is what enforces "single source
    of truth" going forward.
    """
    from alembic import command
    from alembic.util import AutogenerateDiffsDetected

    db = tmp_path / "guardrail.db"
    url = _sqlite_url_for(db)
    alembic_runner.upgrade(url)  # DB now at head

    cfg = _make_config(url)
    try:
        command.check(cfg)
    except AutogenerateDiffsDetected as exc:
        pytest.fail(
            f"models drifted from head revision "
            f"(run: uv run alembic revision --autogenerate -m '...'): {exc}"
        )
