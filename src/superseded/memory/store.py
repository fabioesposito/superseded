from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from superseded.memory._stats_sql import STATS_FILE_PATTERN_CASE

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(".superseded/memory.db")

# SQLite locking resilience. The sqlite3 default busy_timeout is 5s, which is
# too short when a concurrent review / migration / WAL checkpoint holds the
# write lock (the typical trigger for intermittent "database is locked" errors
# after an aborted or erroring review). 15s gives those windows room; the retry
# loop below is the safety net for the rare case where a stuck peer outlasts
# even that.
_BUSY_TIMEOUT_MS = 15_000
_LOCK_RETRY_MAX = 6
_LOCK_RETRY_BASE_DELAY = 0.2


async def _apply_sqlite_pragmas(db: aiosqlite.Connection) -> None:
    await db.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    await db.execute("PRAGMA journal_mode=WAL")


def _is_locked_error(err: BaseException) -> bool:
    return isinstance(err, sqlite3.OperationalError) and "locked" in str(err).lower()


async def _retry_locked[T](op: Callable[[], Awaitable[T]]) -> T:
    """Run ``op`` and retry on transient SQLite lock errors.

    sqlite3's busy handler covers most contention, but a few lock paths (e.g. a
    peer stuck mid-transaction after an aborted review) can still surface
    SQLITE_BUSY past the busy_timeout. The whole connection block lives inside
    ``op`` so each attempt re-acquires the connection. Only safe for idempotent
    writes; do not use it for plain INSERTs like ``record_feedback``.
    """
    for attempt in range(_LOCK_RETRY_MAX):
        try:
            return await op()
        except sqlite3.OperationalError as err:
            if not _is_locked_error(err) or attempt == _LOCK_RETRY_MAX - 1:
                raise
            delay = _LOCK_RETRY_BASE_DELAY * (2**attempt)
            logger.debug(
                "sqlite locked, retry %d/%d after %.2fs", attempt + 1, _LOCK_RETRY_MAX, delay
            )
            await asyncio.sleep(delay)
    raise RuntimeError("unreachable")  # pragma: no cover


class MemoryStore:
    """SQLite-backed memory with an optional long-lived connection.

    Callers that perform many operations (the CLI persisting N findings, the
    server worker recording a review) should use ``async with store:`` so all
    operations reuse a single connection. Each ``init()``/``open()`` brings the
    schema to the Alembic head revision via ``alembic_runner.upgrade``
    (idempotent; safe to call repeatedly).
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DEFAULT_DB_PATH
        self._conn: aiosqlite.Connection | None = None

    async def __aenter__(self) -> MemoryStore:
        await self.open()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def open(self) -> None:
        """Open (and migrate) a long-lived connection. Idempotent."""
        import asyncio

        from superseded.memory import alembic_runner

        if self._conn is not None:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+aiosqlite:///{self.db_path.resolve()}"
        await asyncio.to_thread(alembic_runner.upgrade, url)
        with contextlib.suppress(OSError):
            os.chmod(self.db_path, 0o600)
        self._conn = await aiosqlite.connect(self.db_path)
        await _apply_sqlite_pragmas(self._conn)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            # Roll back any uncommitted transaction so an aborted/interrupted
            # write never leaks a write lock onto the next caller (matters for
            # the server's shared long-lived connection).
            with contextlib.suppress(Exception):
                await self._conn.rollback()
            await self._conn.close()
            self._conn = None

    @asynccontextmanager
    async def _db(self) -> AsyncIterator[aiosqlite.Connection]:
        """Yield the long-lived connection when open, else a one-shot connection."""
        if self._conn is not None:
            yield self._conn
            return
        async with aiosqlite.connect(self.db_path) as db:
            await _apply_sqlite_pragmas(db)
            yield db

    async def _write[T](self, op: Callable[[aiosqlite.Connection], Awaitable[T]]) -> T:
        """Run a write block against a connection with lock-error retry.

        Each retry re-enters ``self._db()`` so it works for both the long-lived
        connection and one-shot connections. Only use this for idempotent writes.
        """

        async def _attempt() -> T:
            async with self._db() as db:
                return await op(db)

        return await _retry_locked(_attempt)

    async def init(self) -> None:
        """Ensure the schema exists and migrations are at head.

        Idempotent. Prefer ``async with store:`` for batched work; this is the
        back-compat entrypoint for callers that historically called ``init()``
        before each operation.
        """
        import asyncio

        from superseded.memory import alembic_runner

        if self._conn is not None:
            return  # open() already migrated.
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+aiosqlite:///{self.db_path.resolve()}"
        await asyncio.to_thread(alembic_runner.upgrade, url)
        async with aiosqlite.connect(self.db_path) as db:
            with contextlib.suppress(OSError):
                os.chmod(self.db_path, 0o600)
            await _apply_sqlite_pragmas(db)
            await db.commit()

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
        async def _do(db: aiosqlite.Connection) -> None:
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

        await self._write(_do)

    async def set_comment_id(self, finding_id: str, comment_id: int) -> None:
        async def _do(db: aiosqlite.Connection) -> None:
            await db.execute(
                "UPDATE findings SET comment_id = ? WHERE id = ?",
                (comment_id, finding_id),
            )
            await db.commit()

        await self._write(_do)

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
        # Intentionally NOT wrapped in _write: this is a plain INSERT, so a
        # retry after a partial commit could duplicate feedback rows.
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
        async def _do(db: aiosqlite.Connection) -> None:
            await db.execute(
                "INSERT OR REPLACE INTO installations (app_installation_id, owner, repos) "
                "VALUES (?, ?, ?)",
                (installation_id, owner, json.dumps(repos)),
            )
            await db.commit()

        await self._write(_do)

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
        async def _do(db: aiosqlite.Connection) -> None:
            await db.execute(
                "DELETE FROM installations WHERE app_installation_id = ?",
                (installation_id,),
            )
            await db.commit()

        await self._write(_do)

    async def get_watermark(self, repo: str, pr_number: int) -> str | None:
        async with self._db() as db:
            cursor = await db.execute(
                "SELECT head_sha FROM review_watermarks WHERE repo = ? AND pr_number = ?",
                (repo, pr_number),
            )
            row = await cursor.fetchone()
            return row[0] if row is not None else None

    async def set_watermark(self, repo: str, pr_number: int, head_sha: str) -> None:
        async def _do(db: aiosqlite.Connection) -> None:
            await db.execute(
                "INSERT INTO review_watermarks (repo, pr_number, head_sha) VALUES (?, ?, ?) "
                "ON CONFLICT(repo, pr_number) DO UPDATE SET head_sha = excluded.head_sha",
                (repo, pr_number, head_sha),
            )
            await db.commit()

        await self._write(_do)

    async def get_learned_rules(self, repo: str, limit: int = 5) -> list[dict]:
        """Return top rules for repo, sorted by confidence desc, created_at desc.
        Also updates last_applied_at for the returned rules. Filters out rules with confidence < 0.3."""
        async with self._db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM learned_rules "
                "WHERE repo = ? AND confidence >= 0.3 "
                "ORDER BY confidence DESC, created_at DESC "
                "LIMIT ?",
                (repo, limit),
            )
            rows = await cursor.fetchall()
            rules = [dict(row) for row in rows]
            if rules:
                ids = [r["id"] for r in rules]
                placeholders = ",".join("?" for _ in ids)

                async def _do(db: aiosqlite.Connection) -> None:
                    await db.execute(
                        f"UPDATE learned_rules SET last_applied_at = CURRENT_TIMESTAMP "
                        f"WHERE id IN ({placeholders})",
                        ids,
                    )
                    await db.commit()

                await self._write(_do)
            return rules

    async def get_reflection_state(self, repo: str) -> int:
        """Return last_feedback_id or 0 if no row exists."""
        async with self._db() as db:
            cursor = await db.execute(
                "SELECT last_feedback_id FROM reflection_state WHERE repo = ?",
                (repo,),
            )
            row = await cursor.fetchone()
            return row[0] if row is not None else 0

    async def set_reflection_state(self, repo: str, last_feedback_id: int) -> None:
        """INSERT OR REPLACE INTO reflection_state."""

        async def _do(db: aiosqlite.Connection) -> None:
            await db.execute(
                "INSERT OR REPLACE INTO reflection_state (repo, last_feedback_id) VALUES (?, ?)",
                (repo, last_feedback_id),
            )
            await db.commit()

        await self._write(_do)

    async def refresh_review_stats(self, repo: str) -> None:
        async def _do(db: aiosqlite.Connection) -> None:
            await db.execute(
                f"INSERT INTO review_stats "
                f"(repo, pass, severity, file_pattern, total, accepted, dismissed) "
                f"SELECT f.repo, f.pass, f.severity, "
                f"{STATS_FILE_PATTERN_CASE} AS file_pattern, "
                f"COUNT(*) AS total, "
                f"COUNT(*) FILTER (WHERE fb.action = 'helpful') AS accepted, "
                f"COUNT(*) FILTER (WHERE fb.action = 'dismiss') AS dismissed "
                f"FROM findings f "
                f"JOIN feedback fb ON fb.finding_id = f.id "
                f"WHERE f.repo = ? "
                f"GROUP BY f.repo, f.pass, f.severity, file_pattern "
                f"ON CONFLICT(repo, pass, severity, file_pattern) DO UPDATE SET "
                f"total = excluded.total, "
                f"accepted = excluded.accepted, "
                f"dismissed = excluded.dismissed, "
                f"updated_at = CURRENT_TIMESTAMP",
                (repo,),
            )
            await db.commit()

        await self._write(_do)

    async def get_review_stats(self, repo: str, min_sample: int) -> list[dict]:
        async with self._db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM review_stats WHERE repo = ? AND total >= ?",
                (repo, min_sample),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_installation_config(self, installation_id: int) -> dict[str, str]:
        async with self._db() as db:
            rows = await db.execute_fetchall(
                "SELECT key, value FROM installation_config WHERE installation_id = ?",
                (installation_id,),
            )
            return {row[0]: row[1] for row in rows}

    async def set_installation_config(self, installation_id: int, key: str, value: str) -> None:
        async def _do(db: aiosqlite.Connection) -> None:
            await db.execute(
                "INSERT OR REPLACE INTO installation_config (installation_id, key, value) "
                "VALUES (?, ?, ?)",
                (installation_id, key, value),
            )
            await db.commit()

        await self._write(_do)

    async def prune_stale_rules(self, repo: str, max_age_days: int = 30) -> int:
        """Delete learned rules not applied within ``max_age_days``.

        Returns the number of deleted rules.
        """

        async def _do(db: aiosqlite.Connection) -> int:
            cursor = await db.execute(
                "DELETE FROM learned_rules "
                "WHERE repo = ? "
                "AND (last_applied_at IS NULL OR "
                "last_applied_at < datetime('now', '-' || ? || ' days'))",
                (repo, max_age_days),
            )
            await db.commit()
            return cursor.rowcount

        return await self._write(_do)

    async def dismiss_learned_rule(self, rule_id: int) -> bool:
        """Halve the confidence of a learned rule. Returns False if not found."""

        async def _do(db: aiosqlite.Connection) -> bool:
            cursor = await db.execute(
                "UPDATE learned_rules SET confidence = confidence * 0.5 WHERE id = ?",
                (rule_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

        return await self._write(_do)

    async def reinforce_learned_rule(self, rule_id: int) -> bool:
        """Increment confidence by 0.1 (capped at 1.0). Returns False if not found."""

        async def _do(db: aiosqlite.Connection) -> bool:
            cursor = await db.execute(
                "UPDATE learned_rules SET confidence = MIN(1.0, confidence + 0.1) WHERE id = ?",
                (rule_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

        return await self._write(_do)

    async def get_all_learned_rules(self, repo: str) -> list[dict]:
        """Return all learned rules for a repo (including stale/low-confidence)."""
        async with self._db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM learned_rules WHERE repo = ? ORDER BY confidence DESC, created_at DESC",
                (repo,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
