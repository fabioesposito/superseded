from __future__ import annotations

MAX_RULES = 5


def assemble_learned_context(
    stats_text: str | None,
    rules: list[dict],
    max_rules: int = MAX_RULES,
) -> str | None:
    if stats_text is None and not rules:
        return None

    sorted_rules = sorted(rules, key=lambda r: r["created_at"], reverse=True)
    sorted_rules = sorted(sorted_rules, key=lambda r: -r["confidence"])
    sorted_rules = sorted_rules[:max_rules]

    lines = ["Based on past review outcomes, the team has implicit preferences:"]

    if stats_text is not None:
        lines.append("")
        lines.append("**Statistical guidance:**")
        lines.append(stats_text)

    if sorted_rules:
        lines.append("")
        lines.append("**Inferred rules:**")
        for i, rule in enumerate(sorted_rules, 1):
            conf = rule["confidence"]
            count = rule["evidence_count"]
            lines.append(f"{i}. {rule['rule_text']} (confidence: {conf:.0%}, {count} dismissal(s))")

    return "\n".join(lines)
