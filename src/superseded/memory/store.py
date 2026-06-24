from __future__ import annotations

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
"""


class MemoryStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DEFAULT_DB_PATH

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA)
            await self._migrate(db)

    async def _migrate(self, db: aiosqlite.Connection) -> None:
        cursor = await db.execute("PRAGMA table_info(findings)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "comment_id" not in columns:
            await db.execute("ALTER TABLE findings ADD COLUMN comment_id INTEGER")

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
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO findings (id, repo, pass, severity, file, line, title, description) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (finding_id, repo, pass_name, severity, file, line, title, description),
            )
            await db.commit()

    async def set_comment_id(self, finding_id: str, comment_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE findings SET comment_id = ? WHERE id = ?",
                (comment_id, finding_id),
            )
            await db.commit()

    async def get_finding_by_comment_id(self, comment_id: int) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM findings WHERE comment_id = ?",
                (comment_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row is not None else None

    async def record_feedback(self, finding_id: str, action: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM findings WHERE repo = ? AND dismissed = TRUE",
                (repo,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
