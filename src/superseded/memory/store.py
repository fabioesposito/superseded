from __future__ import annotations

import contextlib
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

DEFAULT_DB_PATH = Path(".superseded/memory.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    repo TEXT,
    pass TEXT,
    severity TEXT,
    file TEXT,
    line INTEGER,
    reasoning TEXT DEFAULT '',
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

CREATE TABLE IF NOT EXISTS installations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_installation_id INTEGER UNIQUE NOT NULL,
    owner TEXT NOT NULL,
    repos TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class MemoryStore:
    """SQLite-backed memory with an optional long-lived connection.

    Callers that perform many operations (the CLI persisting N findings, the
    server worker recording a review) should use ``async with store:`` so all
    operations reuse a single connection. Each ``init()``/``open()`` runs the
    ALTER TABLE migration exactly once per instance.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DEFAULT_DB_PATH
        self._conn: aiosqlite.Connection | None = None
        self._migrated = False

    async def __aenter__(self) -> MemoryStore:
        await self.open()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def open(self) -> None:
        """Open (and migrate) a long-lived connection. Idempotent."""
        if self._conn is not None:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(SCHEMA)
        with contextlib.suppress(OSError):
            os.chmod(self.db_path, 0o600)
        if not self._migrated:
            await self._migrate(self._conn)
            self._migrated = True
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @asynccontextmanager
    async def _db(self) -> AsyncIterator[aiosqlite.Connection]:
        """Yield the long-lived connection when open, else a one-shot connection."""
        if self._conn is not None:
            yield self._conn
            return
        async with aiosqlite.connect(self.db_path) as db:
            yield db

    async def init(self) -> None:
        """Ensure the schema exists and migration has run once.

        Idempotent. Prefer ``async with store:`` for batched work; this is the
        back-compat entrypoint for callers that historically called ``init()``
        before each operation. Migration runs at most once per instance.
        """
        if self._conn is not None:
            return  # open() already created the schema + migrated.
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript(SCHEMA)
            with contextlib.suppress(OSError):
                os.chmod(self.db_path, 0o600)
            if not self._migrated:
                await self._migrate(db)
                self._migrated = True
            await db.commit()

    async def _migrate(self, db: aiosqlite.Connection) -> None:
        cursor = await db.execute("PRAGMA table_info(findings)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "comment_id" not in columns:
            await db.execute("ALTER TABLE findings ADD COLUMN comment_id INTEGER")
        if "reasoning" not in columns:
            await db.execute("ALTER TABLE findings ADD COLUMN reasoning TEXT DEFAULT ''")

    async def record_finding(
        self,
        finding_id: str,
        repo: str,
        pass_name: str,
        severity: str,
        file: str,
        line: int,
        title: str,
        description: str,
        reasoning: str = "",
    ) -> None:
        async with self._db() as db:
            await db.execute(
                "INSERT INTO findings "
                "(id, repo, pass, severity, file, line, title, description, reasoning) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "severity = excluded.severity, "
                "description = excluded.description, "
                "reasoning = excluded.reasoning "
                "WHERE excluded.severity != severity "
                "OR excluded.description != description "
                "OR excluded.reasoning != reasoning",
                (finding_id, repo, pass_name, severity, file, line, title, description, reasoning),
            )
            await db.commit()

    async def set_comment_id(self, finding_id: str, comment_id: int) -> None:
        async with self._db() as db:
            await db.execute(
                "UPDATE findings SET comment_id = ? WHERE id = ?",
                (comment_id, finding_id),
            )
            await db.commit()

    async def get_finding_by_comment_id(self, comment_id: int) -> dict | None:
        async with self._db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM findings WHERE comment_id = ?",
                (comment_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row is not None else None

    async def record_feedback(self, finding_id: str, action: str) -> None:
        async with self._db() as db:
            await db.execute(
                "INSERT INTO feedback (finding_id, action) VALUES (?, ?)",
                (finding_id, action),
            )
            if action == "dismiss":
                await db.execute(
                    "UPDATE findings SET dismissed = TRUE WHERE id = ?",
                    (finding_id,),
                )
            await db.commit()

    async def record_feedback_by_comment_id(self, comment_id: int, action: str) -> bool:
        finding = await self.get_finding_by_comment_id(comment_id)
        if finding is None:
            return False
        await self.record_feedback(finding["id"], action)
        return True

    async def get_dismissed_findings(self, repo: str) -> list[dict]:
        async with self._db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM findings WHERE repo = ? AND dismissed = TRUE",
                (repo,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def record_installation(self, installation_id: int, owner: str, repos: list[str]) -> None:
        async with self._db() as db:
            await db.execute(
                "INSERT OR REPLACE INTO installations (app_installation_id, owner, repos) "
                "VALUES (?, ?, ?)",
                (installation_id, owner, json.dumps(repos)),
            )
            await db.commit()

    async def get_installation(self, installation_id: int) -> dict | None:
        async with self._db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM installations WHERE app_installation_id = ?",
                (installation_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row is not None else None

    async def remove_installation(self, installation_id: int) -> None:
        async with self._db() as db:
            await db.execute(
                "DELETE FROM installations WHERE app_installation_id = ?",
                (installation_id,),
            )
            await db.commit()
