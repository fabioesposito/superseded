"""Programmatic Alembic runner: URL normalization, probe, stamp, upgrade.

Public API:
- ``normalize_url(url)`` — convert superseded-style URLs to SQLAlchemy async
  dialect URLs.
- ``upgrade(url)`` — probe for pre-Alembic state, stamp if needed, upgrade to
  head; return the resulting revision string. Synchronous (call via
  ``asyncio.to_thread`` from async code).
"""

from __future__ import annotations

import asyncio
import contextlib
from importlib.resources import files

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine

_BASELINE_REVISION = "0001"


def normalize_url(url: str) -> str:
    """Convert a superseded-style DB URL to a SQLAlchemy async-dialect URL.

    - ``sqlite://...``           -> ``sqlite+aiosqlite://...``
    - ``postgres://...``          -> ``postgresql+asyncpg://...``
    - ``postgresql://...``        -> ``postgresql+asyncpg://...``
    - already-async forms
      (``sqlite+aiosqlite://...``, ``postgresql+asyncpg://...``) -> unchanged
    """
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    if url.startswith("sqlite://"):
        return "sqlite+aiosqlite://" + url[len("sqlite://") :]
    return url


def _make_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(files("superseded.memory.migrations")))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


async def _probe(url: str) -> tuple[bool, bool]:
    """Return ``(has_alembic_version, has_findings)``.

    - ``has_alembic_version`` True  -> already an Alembic-managed DB.
    - ``has_findings``        True  -> DB has real schema tables.
    The caller decides stamp/fresh from the combination.
    """
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:

            def _inspect(sync_conn):
                from sqlalchemy import inspect

                insp = inspect(sync_conn)
                return insp.has_table("alembic_version"), insp.has_table("findings")

            return await conn.run_sync(_inspect)
    finally:
        with contextlib.suppress(Exception):
            await engine.dispose()


def _current_revision(url: str) -> str | None:
    """Read the current Alembic revision from the DB at ``url``."""

    async def _get() -> str | None:
        engine = create_async_engine(url)

        def _read(sync_conn) -> str | None:
            ctx = MigrationContext.configure(sync_conn)
            return ctx.get_current_revision()

        try:
            async with engine.connect() as conn:
                return await conn.run_sync(_read)
        finally:
            with contextlib.suppress(Exception):
                await engine.dispose()

    return asyncio.run(_get())


def upgrade(url: str) -> str:
    """Probe, stamp a pre-Alembic DB if needed, and upgrade to head.

    Returns the current (head) revision. Safe to call repeatedly.
    """
    normalized = normalize_url(url)
    cfg = _make_config(normalized)

    has_av, has_findings = asyncio.run(_probe(normalized))
    if not has_av and has_findings:
        command.stamp(cfg, _BASELINE_REVISION)

    command.upgrade(cfg, "head")
    return _current_revision(normalized)
