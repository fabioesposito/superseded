from __future__ import annotations

from superseded.models import Finding, ReviewResult

SEVERITY_ORDER = {"critical": 0, "important": 1, "suggestion": 2, "nit": 3}


def deduplicate(finding_groups: list[list[Finding]]) -> list[Finding]:
    seen: dict[str, Finding] = {}
    for group in finding_groups:
        for f in group:
            if f.id not in seen:
                seen[f.id] = f
    return list(seen.values())


def rank_by_severity(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 99))


def merge_findings(finding_groups: list[list[Finding]]) -> ReviewResult:
    deduped = deduplicate(finding_groups)
    ranked = rank_by_severity(deduped)
    return ReviewResult(findings=ranked)
