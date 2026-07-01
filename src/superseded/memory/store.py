from __future__ import annotations

import contextlib
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from superseded.memory._stats_sql import STATS_FILE_PATTERN_CASE

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

CREATE TABLE IF NOT EXISTS review_watermarks (
    repo        TEXT    NOT NULL,
    pr_number   INTEGER NOT NULL,
    head_sha    TEXT    NOT NULL,
    reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (repo, pr_number)
);

CREATE TABLE IF NOT EXISTS review_stats (
    repo         TEXT    NOT NULL,
    pass         TEXT    NOT NULL,
    severity     TEXT    NOT NULL,
    file_pattern TEXT    NOT NULL DEFAULT '*',
    total        INTEGER NOT NULL DEFAULT 0,
    accepted     INTEGER NOT NULL DEFAULT 0,
    dismissed    INTEGER NOT NULL DEFAULT 0,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (repo, pass, severity, file_pattern)
);

CREATE TABLE IF NOT EXISTS learned_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    repo            TEXT    NOT NULL,
    rule_text       TEXT    NOT NULL,
    evidence_count  INTEGER NOT NULL DEFAULT 0,
    confidence      REAL    NOT NULL DEFAULT 1.0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_applied_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reflection_state (
    repo               TEXT    NOT NULL,
    last_feedback_id   INTEGER NOT NULL,
    last_reflection_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (repo)
);

CREATE TABLE IF NOT EXISTS installation_config (
    installation_id INTEGER NOT NULL,
    key             TEXT    NOT NULL,
    value           TEXT    NOT NULL,
    PRIMARY KEY (installation_id, key),
    FOREIGN KEY (installation_id) REFERENCES installations(id) ON DELETE CASCADE
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
        await db.execute(
            "CREATE TABLE IF NOT EXISTS review_watermarks ("
            "repo TEXT NOT NULL, "
            "pr_number INTEGER NOT NULL, "
            "head_sha TEXT NOT NULL, "
            "reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY (repo, pr_number))"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS review_stats ("
            "repo TEXT NOT NULL, "
            "pass TEXT NOT NULL, "
            "severity TEXT NOT NULL, "
            "file_pattern TEXT NOT NULL DEFAULT '*', "
            "total INTEGER NOT NULL DEFAULT 0, "
            "accepted INTEGER NOT NULL DEFAULT 0, "
            "dismissed INTEGER NOT NULL DEFAULT 0, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY (repo, pass, severity, file_pattern))"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS learned_rules ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "repo TEXT NOT NULL, "
            "rule_text TEXT NOT NULL, "
            "evidence_count INTEGER NOT NULL DEFAULT 0, "
            "confidence REAL NOT NULL DEFAULT 1.0, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "last_applied_at TIMESTAMP)"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS reflection_state ("
            "repo TEXT NOT NULL, "
            "last_feedback_id INTEGER NOT NULL, "
            "last_reflection_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY (repo))"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS installation_config ("
            "installation_id INTEGER NOT NULL, "
            "key TEXT NOT NULL, "
            "value TEXT NOT NULL, "
            "PRIMARY KEY (installation_id, key), "
            "FOREIGN KEY (installation_id) REFERENCES installations(id) ON DELETE CASCADE)"
        )

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

    async def get_watermark(self, repo: str, pr_number: int) -> str | None:
        async with self._db() as db:
            cursor = await db.execute(
                "SELECT head_sha FROM review_watermarks WHERE repo = ? AND pr_number = ?",
                (repo, pr_number),
            )
            row = await cursor.fetchone()
            return row[0] if row is not None else None

    async def set_watermark(self, repo: str, pr_number: int, head_sha: str) -> None:
        async with self._db() as db:
            await db.execute(
                "INSERT INTO review_watermarks (repo, pr_number, head_sha) VALUES (?, ?, ?) "
                "ON CONFLICT(repo, pr_number) DO UPDATE SET head_sha = excluded.head_sha",
                (repo, pr_number, head_sha),
            )
            await db.commit()

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
                await db.execute(
                    f"UPDATE learned_rules SET last_applied_at = CURRENT_TIMESTAMP "
                    f"WHERE id IN ({placeholders})",
                    ids,
                )
                await db.commit()
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
        async with self._db() as db:
            await db.execute(
                "INSERT OR REPLACE INTO reflection_state (repo, last_feedback_id) VALUES (?, ?)",
                (repo, last_feedback_id),
            )
            await db.commit()

    async def refresh_review_stats(self, repo: str) -> None:
        async with self._db() as db:
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
        async with self._db() as db:
            await db.execute(
                "INSERT OR REPLACE INTO installation_config (installation_id, key, value) "
                "VALUES (?, ?, ?)",
                (installation_id, key, value),
            )
            await db.commit()

    async def prune_stale_rules(self, repo: str, max_age_days: int = 30) -> int:
        """Delete learned rules not applied within ``max_age_days``.

        Returns the number of deleted rules.
        """
        async with self._db() as db:
            cursor = await db.execute(
                "DELETE FROM learned_rules "
                "WHERE repo = ? "
                "AND (last_applied_at IS NULL OR "
                "last_applied_at < datetime('now', '-' || ? || ' days'))",
                (repo, max_age_days),
            )
            await db.commit()
            return cursor.rowcount

    async def dismiss_learned_rule(self, rule_id: int) -> bool:
        """Halve the confidence of a learned rule. Returns False if not found."""
        async with self._db() as db:
            cursor = await db.execute(
                "UPDATE learned_rules SET confidence = confidence * 0.5 WHERE id = ?",
                (rule_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def reinforce_learned_rule(self, rule_id: int) -> bool:
        """Increment confidence by 0.1 (capped at 1.0). Returns False if not found."""
        async with self._db() as db:
            cursor = await db.execute(
                "UPDATE learned_rules SET confidence = MIN(1.0, confidence + 0.1) WHERE id = ?",
                (rule_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

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
