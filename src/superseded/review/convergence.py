from __future__ import annotations

from dataclasses import dataclass

from superseded.models import Finding

# Severities that block a PR merge and must be carried across review rounds
# even when they are no longer re-flagged in the latest round.
BLOCKING_SEVERITIES = ("critical", "important")


@dataclass(frozen=True)
class PriorFinding:
    fingerprint: str
    severity: str
    file: str
    line: int
    title: str
    comment_id: int | None
    status: str


@dataclass
class ConvergencePlan:
    findings_to_post: list[Finding]
    suppressed_count: int
    carried_findings: list[PriorFinding]
    round_number: int
    at_round_cap: bool
    note: str


def prior_from_rows(rows: list[dict]) -> list[PriorFinding]:
    """Build :class:`PriorFinding` objects from ``pr_findings`` row dicts.

    ``comment_id`` and ``line`` are optional in the persisted row shape; a
    missing/``None`` value collapses to ``None`` and ``0`` respectively.
    """
    prior: list[PriorFinding] = []
    for row in rows:
        prior.append(
            PriorFinding(
                fingerprint=row["fingerprint"],
                severity=row["severity"],
                file=row["file"],
                line=row.get("line") or 0,
                title=row["title"],
                comment_id=row.get("comment_id"),
                status=row["status"],
            )
        )
    return prior


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'s' if count != 1 else ''}"


def plan_convergence(
    findings: list[Finding],
    prior: list[PriorFinding],
    round_number: int,
    max_rounds: int = 3,
) -> ConvergencePlan:
    """Decide which new findings to post this round, suppressing duplicates.

    A new finding is suppressed when it matches a prior finding whose status is
    ``open`` (already reported) or ``dismissed`` (ruled noise). A match on a
    ``resolved`` prior finding is re-posted so the caller can reopen the thread.
    Once ``round_number`` exceeds ``max_rounds``, only ``critical`` findings are
    posted; every other new finding is suppressed.
    """
    by_fingerprint = {p.fingerprint: p for p in prior}
    at_round_cap = round_number > max_rounds

    findings_to_post: list[Finding] = []
    dedupe_suppressed = 0
    round_cap_suppressed = 0

    for finding in findings:
        prior_finding = by_fingerprint.get(finding.fingerprint)
        if prior_finding is not None and prior_finding.status in ("open", "dismissed"):
            dedupe_suppressed += 1
            continue
        if at_round_cap and finding.severity != "critical":
            round_cap_suppressed += 1
            continue
        findings_to_post.append(finding)

    carried_findings = [
        p for p in prior if p.status == "open" and p.severity in BLOCKING_SEVERITIES
    ]

    note_parts: list[str] = []
    if dedupe_suppressed:
        note_parts.append(f"{_plural(dedupe_suppressed, 'duplicate finding')} suppressed")
    if round_cap_suppressed:
        note_parts.append(
            f"{_plural(round_cap_suppressed, 'non-critical finding')} suppressed at round cap"
        )
    if carried_findings:
        note_parts.append(
            f"{_plural(len(carried_findings), 'blocking finding')} still open from earlier rounds"
        )

    return ConvergencePlan(
        findings_to_post=findings_to_post,
        suppressed_count=dedupe_suppressed + round_cap_suppressed,
        carried_findings=carried_findings,
        round_number=round_number,
        at_round_cap=at_round_cap,
        note="; ".join(note_parts),
    )
