from __future__ import annotations

from superseded.models import ReviewResult

SEVERITY_ICONS = {
    "critical": "🔴",
    "important": "🟠",
    "suggestion": "🟡",
    "nit": "⚪",
}


def format_table(result: ReviewResult) -> str:
    if not result.findings:
        return "No issues found."

    lines = []
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

    return "\n".join(lines)
