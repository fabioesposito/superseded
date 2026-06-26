from __future__ import annotations

from superseded.models import Finding, ReviewResult

SEVERITY_ORDER = {"critical": 0, "important": 1, "suggestion": 2, "nit": 3}


def deduplicate(finding_groups: list[list[Finding]]) -> list[Finding]:
    # Dedupe by file+line+title so two passes flagging the same issue collapse
    # to a single finding. `Finding.id` embeds `pass_name` and is kept as the
    # persisted identity for the memory store, so dedup uses a separate key.
    seen: dict[str, Finding] = {}
    for group in finding_groups:
        for f in group:
            key = f.dedup_key
            if key not in seen:
                seen[key] = f
    return list(seen.values())


def rank_by_severity(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 99))


def merge_findings(finding_groups: list[list[Finding]]) -> ReviewResult:
    deduped = deduplicate(finding_groups)
    ranked = rank_by_severity(deduped)
    return ReviewResult(findings=ranked)
