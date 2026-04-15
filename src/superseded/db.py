from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

import aiosqlite
from alembic import command
from alembic.config import Config

from superseded.models import (
    AgentEvent,
    HarnessIteration,
    Issue,
    IssueStatus,
    SessionTurn,
    Stage,
    StageResult,
)

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._id_lock = asyncio.Lock()

    def _run_migrations_sync(self) -> None:
        src_based = Path(__file__).resolve().parent.parent.parent / "migrations"
        cwd_based = Path.cwd() / "migrations"
        migrations_dir = str(src_based if src_based.is_dir() else cwd_based)
        db_path_resolved = str(Path(self.db_path).resolve())
        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", migrations_dir)
        alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path_resolved}")

        conn = sqlite3.connect(db_path_resolved)
        try:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
            )
            has_alembic_version = cursor.fetchone() is not None

            if not has_alembic_version:
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='issues'"
                )
                has_issues = cursor.fetchone() is not None

                if has_issues:
                    command.stamp(alembic_cfg, "head")
                    logger.info("Stamped existing database at head revision")
        finally:
            conn.close()

        command.upgrade(alembic_cfg, "head")

    async def initialize(self) -> None:
        if self._conn is not None:
            return
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.db_path)
        try:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.commit()
            await asyncio.get_running_loop().run_in_executor(None, self._run_migrations_sync)
        except Exception:
            await conn.close()
            raise
        self._conn = conn

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        return self._conn

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def upsert_issue(self, issue: Issue) -> None:
        conn = self._require_conn()
        await conn.execute(
            """INSERT INTO issues (id, title, status, stage, assignee, labels, filepath, created, pause_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET title=?, status=?, stage=?, assignee=?, labels=?, filepath=?, pause_reason=?, updated_at=CURRENT_TIMESTAMP""",
            (
                issue.id,
                issue.title,
                issue.status.value,
                issue.stage.value,
                issue.assignee,
                json.dumps(issue.labels),
                issue.filepath,
                str(issue.created),
                issue.pause_reason,
                issue.title,
                issue.status.value,
                issue.stage.value,
                issue.assignee,
                json.dumps(issue.labels),
                issue.filepath,
                issue.pause_reason,
            ),
        )
        await conn.commit()

    async def get_issue(self, issue_id: str) -> dict[str, Any] | None:
        conn = self._require_conn()
        cursor = await conn.execute("SELECT * FROM issues WHERE id = ?", (issue_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        cols = [desc[0] for desc in cursor.description]
        result = dict(zip(cols, row, strict=True))
        result["labels"] = json.loads(result["labels"])
        return result

    async def list_issues(self) -> list[dict[str, Any]]:
        conn = self._require_conn()
        cursor = await conn.execute("SELECT * FROM issues ORDER BY id")
        rows = await cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        results = []
        for row in rows:
            d = dict(zip(cols, row, strict=True))
            d["labels"] = json.loads(d["labels"])
            results.append(d)
        return results

    async def update_issue_status(self, issue_id: str, status: IssueStatus, stage: Stage) -> None:
        conn = self._require_conn()
        await conn.execute(
            "UPDATE issues SET status=?, stage=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status.value, stage.value, issue_id),
        )
        await conn.commit()

    async def update_pause_reason(self, issue_id: str, reason: str) -> None:
        conn = self._require_conn()
        await conn.execute(
            "UPDATE issues SET pause_reason = ? WHERE id = ?",
            (reason, issue_id),
        )
        await conn.commit()

    async def save_stage_result(
        self, issue_id: str, result: StageResult, repo: str = "primary"
    ) -> None:
        conn = self._require_conn()
        await conn.execute(
            """INSERT INTO stage_results (issue_id, repo, stage, passed, output, error, artifacts, started_at, finished_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                issue_id,
                repo,
                result.stage.value,
                int(result.passed),
                result.output,
                result.error,
                json.dumps(result.artifacts),
                str(result.started_at) if result.started_at else None,
                str(result.finished_at) if result.finished_at else None,
            ),
        )
        await conn.commit()

    async def get_stage_results(
        self, issue_id: str, repo: str | None = None
    ) -> list[dict[str, Any]]:
        conn = self._require_conn()
        if repo:
            cursor = await conn.execute(
                "SELECT * FROM stage_results WHERE issue_id = ? AND repo = ? ORDER BY id",
                (issue_id, repo),
            )
        else:
            cursor = await conn.execute(
                "SELECT * FROM stage_results WHERE issue_id = ? ORDER BY id",
                (issue_id,),
            )
        rows = await cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        results = []
        for row in rows:
            d = dict(zip(cols, row, strict=True))
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
        repo: str = "primary",
    ) -> None:
        conn = self._require_conn()
        await conn.execute(
            """INSERT INTO harness_iterations (issue_id, repo, attempt, stage, exit_code, output, error, previous_errors)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                issue_id,
                repo,
                iteration.attempt,
                iteration.stage.value,
                exit_code,
                output,
                error,
                json.dumps(iteration.previous_errors),
            ),
        )
        await conn.commit()

    async def get_harness_iterations(self, issue_id: str) -> list[dict[str, Any]]:
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT * FROM harness_iterations WHERE issue_id = ? ORDER BY id",
            (issue_id,),
        )
        rows = await cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        results = []
        for row in rows:
            d = dict(zip(cols, row, strict=True))
            d["previous_errors"] = json.loads(d["previous_errors"])
            results.append(d)
        return results

    async def next_issue_id(self) -> str:
        async with self._id_lock:
            conn = self._require_conn()
            cursor = await conn.execute(
                "SELECT MAX(CAST(SUBSTR(id, 5) AS INTEGER)) FROM issues WHERE id LIKE 'SUP-%'"
            )
            row = await cursor.fetchone()
            max_num = row[0] if row and row[0] else 0
            return f"SUP-{max_num + 1:03d}"

    async def save_session_turn(self, issue_id: str, turn: SessionTurn) -> None:
        conn = self._require_conn()
        await conn.execute(
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
        await conn.commit()

    async def get_session_turns(
        self, issue_id: str, stage: Stage | None = None
    ) -> list[dict[str, Any]]:
        conn = self._require_conn()
        if stage:
            cursor = await conn.execute(
                "SELECT * FROM session_turns WHERE issue_id = ? AND stage = ? ORDER BY id",
                (issue_id, stage.value),
            )
        else:
            cursor = await conn.execute(
                "SELECT * FROM session_turns WHERE issue_id = ? ORDER BY id",
                (issue_id,),
            )
        rows = await cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        results = []
        for row in rows:
            d = dict(zip(cols, row, strict=True))
            d["metadata"] = json.loads(d["metadata"])
            results.append(d)
        return results

    async def save_agent_event(self, issue_id: str, event: AgentEvent) -> None:
        conn = self._require_conn()
        await conn.execute(
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
        await conn.commit()

    async def get_agent_events(self, issue_id: str, limit: int = 200) -> list[dict[str, Any]]:
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT * FROM agent_events WHERE issue_id = ? ORDER BY id DESC LIMIT ?",
            (issue_id, limit),
        )
        rows = await cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        results = []
        for row in rows:
            d = dict(zip(cols, row, strict=True))
            d["metadata"] = json.loads(d["metadata"])
            results.append(d)
        return list(reversed(results))

    async def get_recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT * FROM agent_events ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        results = []
        for row in rows:
            d = dict(zip(cols, row, strict=True))
            d["metadata"] = json.loads(d["metadata"])
            results.append(d)
        return list(reversed(results))
