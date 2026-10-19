from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from superseded.memory.backend import Store


class StatsAggregator:
    MIN_SAMPLE = 5

    def __init__(self, store: Store) -> None:
        self._store = store

    async def get_stats_context(self, repo: str) -> str | None:
        """Query review_stats for repo, format as guidance text.

        Returns None if no rows meet the MIN_SAMPLE threshold.
        """
        rows = await self._store.get_review_stats(repo, self.MIN_SAMPLE)

        if not rows:
            return None

        hints: list[str] = []
        for r in rows:
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
        """Recompute review_stats from findings+feedback for this repo."""
        await self._store.refresh_review_stats(repo)

    async def get_report(self, repo: str) -> dict:
        """Assemble an outcome report for a repo, refreshing stats first.

        Returns overall counts/rates (human dismissals and verifier drops),
        open PR findings, the max review round, and per pass/severity rows.
        """
        await self._refresh(repo)

        counts = await self._store.get_finding_outcome_counts(repo)
        open_pr_findings = await self._store.get_open_pr_finding_count(repo)
        max_review_round = await self._store.get_max_review_round(repo)
        rows = await self._store.get_review_stats(repo, 1)

        total = counts["total"]
        dismissed = counts["dismissed"]
        verifier_dropped = counts["verifier_dropped"]

        by_pass: list[dict] = []
        for r in rows:
            r_total = r["total"]
            by_pass.append(
                {
                    "pass": r["pass"],
                    "severity": r["severity"],
                    "file_pattern": r["file_pattern"],
                    "total": r_total,
                    "accepted": r["accepted"],
                    "dismissed": r["dismissed"],
                    "dismiss_rate": (r["dismissed"] / r_total) if r_total else 0.0,
                }
            )

        return {
            "total": total,
            "dismissed": dismissed,
            "dismiss_rate": (dismissed / total) if total else 0.0,
            "verifier_dropped": verifier_dropped,
            "verifier_drop_rate": (verifier_dropped / total) if total else 0.0,
            "open_pr_findings": open_pr_findings,
            "max_review_round": max_review_round,
            "by_pass": by_pass,
        }
