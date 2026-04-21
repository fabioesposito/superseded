from __future__ import annotations

import logging
from pathlib import Path

import frontmatter

from superseded.db import Database
from superseded.models import IssueStatus, Stage

logger = logging.getLogger(__name__)


class IssueStateWriter:
    """Write issue state to markdown (canonical) then SQLite (cache).

    Markdown is written first. If it fails, SQLite is not touched.
    If markdown succeeds but SQLite fails, a warning is logged —
    markdown remains the canonical source of truth.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    async def write_status(
        self, issue_id: str, filepath: str, status: IssueStatus, stage: Stage
    ) -> None:
        self._write_markdown(filepath, status, stage)
        try:
            await self.db.update_issue_status(issue_id, status, stage)
        except Exception:
            logger.exception("SQLite write failed for %s (markdown is canonical)", issue_id)

    def _write_markdown(self, filepath: str, status: IssueStatus, stage: Stage) -> None:
        path = Path(filepath)
        post = frontmatter.load(path)
        post["status"] = status.value
        post["stage"] = stage.value
        path.write_text(frontmatter.dumps(post))
