from __future__ import annotations

from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from superseded.memory.store import MemoryStore


def _classify_file_pattern(file: str) -> str:
    if (
        file.startswith("test/")
        or file.startswith("tests/")
        or "_test." in file
        or file.startswith("test_")
        or "__test__/" in file
    ):
        return "test"
    if "migrations/" in file:
        return "migration"
    if file.endswith((".yaml", ".yml", ".toml", ".json")) or file.startswith("Dockerfile"):
        return "config"
    return "*"


_CASE_EXPR = """\
CASE
    WHEN f.file LIKE 'test/%' OR f.file LIKE 'tests/%'
         OR f.file LIKE '%%_test.%%' OR f.file LIKE 'test_%%'
         OR f.file LIKE '%%__test__/%%' THEN 'test'
    WHEN f.file LIKE '%%migrations/%%' THEN 'migration'
    WHEN f.file LIKE '%%.yaml' OR f.file LIKE '%%.yml'
         OR f.file LIKE '%%.toml' OR f.file LIKE '%%.json'
         OR f.file LIKE 'Dockerfile%%' THEN 'config'
    ELSE '*'
END"""


class StatsAggregator:
    MIN_SAMPLE = 5

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def get_stats_context(self, repo: str) -> str | None:
        """Query review_stats for repo, format as guidance text.

        Returns None if no rows meet the MIN_SAMPLE threshold.
        """
        async with self._store._db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM review_stats WHERE repo = ? AND total >= ?",
                (repo, self.MIN_SAMPLE),
            )
            rows = await cursor.fetchall()

        if not rows:
            return None

        hints: list[str] = []
        for row in rows:
            r = dict(row)
            total = r["total"]
            dismiss_rate = r["dismissed"] / total
            accept_rate = r["accepted"] / total
            fp = r["file_pattern"]
            ps = r["pass"]
            sev = r["severity"]

            if dismiss_rate > 0.8 and fp != "*":
                hints.append(
                    f"Suppress {ps}/{sev} findings on {fp} files "
                    f"(dismissal rate {dismiss_rate:.0%})."
                )
            elif dismiss_rate > 0.5:
                hints.append(
                    f"Prefer higher-severity {ps} findings (dismissal rate {dismiss_rate:.0%})."
                )
            elif accept_rate > 0.8:
                hints.append(
                    f"Continue current approach for {ps}/{sev} (acceptance rate {accept_rate:.0%})."
                )

        return "\n".join(hints) if hints else None

    async def _refresh(self, repo: str) -> None:
        """Upsert review_stats from findings+feedback for this repo.

        Only includes findings that have at least one feedback row (INNER JOIN).
        Uses CASE expression for file_pattern classification.
        """
        async with self._store._db() as db:
            await db.execute(
                f"INSERT INTO review_stats "
                f"(repo, pass, severity, file_pattern, total, accepted, dismissed) "
                f"SELECT f.repo, f.pass, f.severity, {_CASE_EXPR} AS file_pattern, "
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
