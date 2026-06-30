from __future__ import annotations

from unittest.mock import patch

import pytest

from superseded.memory.backend import make_store
from superseded.memory.store import DEFAULT_DB_PATH, MemoryStore


def _postgres_available() -> bool:
    try:
        import superseded.memory.postgres  # noqa: F401

        return True
    except ImportError:
        return False


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


@pytest.mark.skipif(not _postgres_available(), reason="PostgresStore not implemented yet")
def test_make_store_postgres_returns_postgres_store():
    with patch("superseded.memory.postgres.PostgresStore") as mock_cls:
        mock_cls.return_value = object()
        store = make_store("postgres://u:p@host/db", max_size=5)
        mock_cls.assert_called_once_with("postgres://u:p@host/db", max_size=5)
        assert store is mock_cls.return_value


@pytest.mark.skipif(not _postgres_available(), reason="PostgresStore not implemented yet")
def test_make_store_postgresql_scheme_also_works():
    with patch("superseded.memory.postgres.PostgresStore") as mock_cls:
        mock_cls.return_value = object()
        make_store("postgresql://u:p@host/db")
        mock_cls.assert_called_once()


def test_make_store_unsupported_scheme_raises():
    with pytest.raises(ValueError, match="Unsupported database scheme"):
        make_store("mysql://u:p@host/db")


def test_memory_store_satisfies_store_protocol_including_dunders():
    """Regression: worker.py does `async with store:` — both backends must
    expose __aenter__/__aexit__. The runtime_checkable Protocol only checks
    attribute existence, so verify the dunders explicitly."""
    from superseded.memory.backend import Store
    from superseded.memory.store import MemoryStore

    s = MemoryStore()
    assert isinstance(s, Store)
    assert hasattr(s, "__aenter__") and hasattr(s, "__aexit__")


def test_postgres_store_satisfies_store_protocol_including_dunders():
    """Same regression guard for the Postgres backend (no DB connection needed:
    PostgresStore('postgres://x') only stores the DSN, doesn't connect)."""
    from superseded.memory.backend import Store
    from superseded.memory.postgres import PostgresStore

    s = PostgresStore("postgres://x")
    assert isinstance(s, Store)
    assert hasattr(s, "__aenter__") and hasattr(s, "__aexit__")
