from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite

from superseded.models import (
    AgentEvent,
    HarnessIteration,
    Issue,
    IssueStatus,
    SessionTurn,
    Stage,
    StageResult,
)


class Database:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS issues (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new',
                stage TEXT NOT NULL DEFAULT 'spec',
                assignee TEXT DEFAULT '',
                labels TEXT DEFAULT '[]',
                filepath TEXT DEFAULT '',
                created TEXT DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS stage_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                passed INTEGER NOT NULL,
                output TEXT DEFAULT '',
                error TEXT DEFAULT '',
                artifacts TEXT DEFAULT '[]',
                started_at TEXT,
                finished_at TEXT,
                FOREIGN KEY (issue_id) REFERENCES issues(id)
            );
            CREATE TABLE IF NOT EXISTS harness_iterations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                stage TEXT NOT NULL,
                exit_code INTEGER NOT NULL,
                output TEXT DEFAULT '',
                error TEXT DEFAULT '',
                previous_errors TEXT DEFAULT '[]',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (issue_id) REFERENCES issues(id)
            );
            CREATE TABLE IF NOT EXISTS session_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (issue_id) REFERENCES issues(id)
            );
            CREATE TABLE IF NOT EXISTS agent_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                event_type TEXT NOT NULL,
                content TEXT DEFAULT '',
                metadata TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (issue_id) REFERENCES issues(id)
            );
        """)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def upsert_issue(self, issue: Issue) -> None:
        assert self._conn
        await self._conn.execute(
            """INSERT INTO issues (id, title, status, stage, assignee, labels, filepath, created)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET title=?, status=?, stage=?, assignee=?, labels=?, filepath=?, updated_at=CURRENT_TIMESTAMP""",
            (
                issue.id,
                issue.title,
                issue.status.value,
                issue.stage.value,
                issue.assignee,
                json.dumps(issue.labels),
                issue.filepath,
                str(issue.created),
                issue.title,
                issue.status.value,
                issue.stage.value,
                issue.assignee,
                json.dumps(issue.labels),
                issue.filepath,
            ),
        )
        await self._conn.commit()

    async def get_issue(self, issue_id: str) -> dict[str, Any] | None:
        assert self._conn
        cursor = await self._conn.execute(
            "SELECT * FROM issues WHERE id = ?", (issue_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        cols = [desc[0] for desc in cursor.description]
        result = dict(zip(cols, row))
        result["labels"] = json.loads(result["labels"])
        return result

    async def list_issues(self) -> list[dict[str, Any]]:
        assert self._conn
        cursor = await self._conn.execute("SELECT * FROM issues ORDER BY id")
        rows = await cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        results = []
        for row in rows:
            d = dict(zip(cols, row))
            d["labels"] = json.loads(d["labels"])
            results.append(d)
        return results

    async def update_issue_status(
        self, issue_id: str, status: IssueStatus, stage: Stage
    ) -> None:
        assert self._conn
        await self._conn.execute(
            "UPDATE issues SET status=?, stage=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status.value, stage.value, issue_id),
        )
        await self._conn.commit()

    async def save_stage_result(self, issue_id: str, result: StageResult) -> None:
        assert self._conn
        await self._conn.execute(
            """INSERT INTO stage_results (issue_id, stage, passed, output, error, artifacts, started_at, finished_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                issue_id,
                result.stage.value,
                int(result.passed),
                result.output,
                result.error,
                json.dumps(result.artifacts),
                str(result.started_at) if result.started_at else None,
                str(result.finished_at) if result.finished_at else None,
            ),
        )
        await self._conn.commit()

    async def get_stage_results(self, issue_id: str) -> list[dict[str, Any]]:
        assert self._conn
        cursor = await self._conn.execute(
            "SELECT * FROM stage_results WHERE issue_id = ? ORDER BY id", (issue_id,)
        )
        rows = await cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        results = []
        for row in rows:
            d = dict(zip(cols, row))
            d["passed"] = bool(d["passed"])
            d["artifacts"] = json.loads(d["artifacts"])
            results.append(d)
        return results

    async def save_harness_iteration(
        self,
        issue_id: str,
        iteration: HarnessIteration,
        exit_code: int,
        output: str,
        error: str,
    ) -> None:
        assert self._conn
        await self._conn.execute(
            """INSERT INTO harness_iterations (issue_id, attempt, stage, exit_code, output, error, previous_errors)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                issue_id,
                iteration.attempt,
                iteration.stage.value,
                exit_code,
                output,
                error,
                json.dumps(iteration.previous_errors),
            ),
        )
        await self._conn.commit()

    async def get_harness_iterations(self, issue_id: str) -> list[dict[str, Any]]:
        assert self._conn
        cursor = await self._conn.execute(
            "SELECT * FROM harness_iterations WHERE issue_id = ? ORDER BY id",
            (issue_id,),
        )
        rows = await cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        results = []
        for row in rows:
            d = dict(zip(cols, row))
            d["previous_errors"] = json.loads(d["previous_errors"])
            results.append(d)
        return results

    async def next_issue_id(self) -> str:
        assert self._conn
        cursor = await self._conn.execute(
            "SELECT MAX(CAST(SUBSTR(id, 5) AS INTEGER)) FROM issues WHERE id LIKE 'SUP-%'"
        )
        row = await cursor.fetchone()
        max_num = row[0] if row and row[0] else 0
        return f"SUP-{max_num + 1:03d}"

    async def save_session_turn(self, issue_id: str, turn: SessionTurn) -> None:
        assert self._conn
        await self._conn.execute(
            """INSERT INTO session_turns (issue_id, stage, attempt, role, content, metadata)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                issue_id,
                turn.stage.value,
                turn.attempt,
                turn.role,
                turn.content,
                json.dumps(turn.metadata),
            ),
        )
        await self._conn.commit()

    async def get_session_turns(
        self, issue_id: str, stage: Stage | None = None
    ) -> list[dict[str, Any]]:
        assert self._conn
        if stage:
            cursor = await self._conn.execute(
                "SELECT * FROM session_turns WHERE issue_id = ? AND stage = ? ORDER BY id",
                (issue_id, stage.value),
            )
        else:
            cursor = await self._conn.execute(
                "SELECT * FROM session_turns WHERE issue_id = ? ORDER BY id",
                (issue_id,),
            )
        rows = await cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        results = []
        for row in rows:
            d = dict(zip(cols, row))
            d["metadata"] = json.loads(d["metadata"])
            results.append(d)
        return results

    async def save_agent_event(self, issue_id: str, event: AgentEvent) -> None:
        assert self._conn
        await self._conn.execute(
            """INSERT INTO agent_events (issue_id, stage, event_type, content, metadata)
               VALUES (?, ?, ?, ?, ?)""",
            (
                issue_id,
                event.stage.value,
                event.event_type,
                event.content,
                json.dumps(event.metadata),
            ),
        )
        await self._conn.commit()

    async def get_agent_events(
        self, issue_id: str, limit: int = 200
    ) -> list[dict[str, Any]]:
        assert self._conn
        cursor = await self._conn.execute(
            "SELECT * FROM agent_events WHERE issue_id = ? ORDER BY id DESC LIMIT ?",
            (issue_id, limit),
        )
        rows = await cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        results = []
        for row in rows:
            d = dict(zip(cols, row))
            d["metadata"] = json.loads(d["metadata"])
            results.append(d)
        return list(reversed(results))

    async def get_recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        assert self._conn
        cursor = await self._conn.execute(
            "SELECT * FROM agent_events ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        results = []
        for row in rows:
            d = dict(zip(cols, row))
            d["metadata"] = json.loads(d["metadata"])
            results.append(d)
        return list(reversed(results))
