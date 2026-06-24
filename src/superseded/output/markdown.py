from __future__ import annotations

from superseded.models import ReviewResult


def format_markdown(result: ReviewResult) -> str:
    if not result.findings:
        return "# Code Review\n\nNo issues found.\n"

    lines = ["# Code Review", ""]

    severity_labels = {
        "critical": "Critical",
        "important": "Important",
        "suggestion": "Suggestion",
        "nit": "Nit",
    }

    for severity in ["critical", "important", "suggestion", "nit"]:
        group = [f for f in result.findings if f.severity == severity]
        if not group:
            continue
        lines.append(f"## {severity_labels[severity]} ({len(group)})")
        lines.append("")
        for f in group:
            lines.append(f"### {f.title}")
            lines.append(f"**{f.file}:{f.line}-{f.end_line}** ({f.pass_name})")
            lines.append("")
            lines.append(f.description)
            lines.append("")
            lines.append(f"**Suggestion:** {f.suggestion}")
            lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)
