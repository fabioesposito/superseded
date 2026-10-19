from __future__ import annotations

from superseded.models import ReviewResult

SEVERITY_ICONS = {
    "critical": "🔴",
    "important": "🟠",
    "suggestion": "🟡",
    "nit": "⚪",
}


def format_table(result: ReviewResult) -> str:
    if not result.findings and not result.carried_findings:
        if result.warnings:
            return "All review passes failed — see stderr for details."
        return "No issues found."

    lines: list[str] = []
    if result.findings:
        header = f"{'Sev':<12} {'Pass':<14} {'File':<30} {'Line':<6} {'Title'}"
        lines.append(header)
        lines.append("-" * len(header))

        for f in result.findings:
            icon = SEVERITY_ICONS.get(f.severity, "⚪")
            lines.append(
                f"{icon} {f.severity:<10} {f.pass_name:<14} {f.file:<30} {f.line:<6} {f.title}"
            )

        lines.append("")
        lines.append(f"Total: {len(result.findings)} findings")
        for sev, count in result.summary.items():
            lines.append(f"  {sev}: {count}")

    if result.carried_findings:
        if lines:
            lines.append("")
        lines.append("Still open from earlier rounds:")
        for f in result.carried_findings:
            icon = SEVERITY_ICONS.get(f.severity, "⚪")
            lines.append(f"  {icon} {f.severity:<10} {f.file}:{f.line}  {f.title}")

    if result.convergence_note:
        lines.append("")
        lines.append(result.convergence_note)

    if result.at_round_cap:
        lines.append("")
        lines.append("Round cap reached — step back and reconsider the approach.")

    return "\n".join(lines)
