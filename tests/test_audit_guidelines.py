from __future__ import annotations

from superseded.audit.guidelines import assemble_learned_context


def test_both_none_returns_none():
    assert assemble_learned_context(None, []) is None


def test_stats_only():
    result = assemble_learned_context("Prefer higher-severity findings.", [])
    assert result is not None
    assert "**Statistical guidance:**" in result
    assert "Prefer higher-severity findings." in result
    assert "**Inferred rules:**" not in result


def test_rules_only():
    rules = [
        {
            "id": 1,
            "repo": "org/repo",
            "rule_text": "Ignore style findings on tests",
            "evidence_count": 5,
            "confidence": 0.85,
            "created_at": "2026-06-30T10:00:00",
            "last_applied_at": "2026-06-30T10:00:00",
        }
    ]
    result = assemble_learned_context(None, rules)
    assert result is not None
    assert "**Inferred rules:**" in result
    assert "**Statistical guidance:**" not in result
    assert "Ignore style findings on tests" in result


def test_both_combined():
    rules = [
        {
            "id": 1,
            "repo": "org/repo",
            "rule_text": "Focus on security issues",
            "evidence_count": 3,
            "confidence": 0.9,
            "created_at": "2026-06-30T10:00:00",
            "last_applied_at": "2026-06-30T10:00:00",
        }
    ]
    result = assemble_learned_context("Some stats.", rules)
    assert result is not None
    assert "**Statistical guidance:**" in result
    assert "Some stats." in result
    assert "**Inferred rules:**" in result
    assert "Focus on security issues" in result


def test_rules_capped_to_max():
    rules = [
        {
            "id": i,
            "repo": "org/repo",
            "rule_text": f"Rule {i}",
            "evidence_count": i,
            "confidence": 1.0 - i * 0.05,
            "created_at": f"2026-06-30T{10 + i:02d}:00:00",
            "last_applied_at": f"2026-06-30T{10 + i:02d}:00:00",
        }
        for i in range(10)
    ]
    result = assemble_learned_context(None, rules, max_rules=3)
    assert result is not None
    assert "1. Rule 0" in result
    assert "2. Rule 1" in result
    assert "3. Rule 2" in result
    assert "Rule 3" not in result


def test_rules_sorted_by_confidence_desc():
    rules = [
        {
            "id": 1,
            "repo": "org/repo",
            "rule_text": "Low confidence rule",
            "evidence_count": 1,
            "confidence": 0.3,
            "created_at": "2026-06-30T10:00:00",
            "last_applied_at": "2026-06-30T10:00:00",
        },
        {
            "id": 2,
            "repo": "org/repo",
            "rule_text": "High confidence rule",
            "evidence_count": 10,
            "confidence": 0.95,
            "created_at": "2026-06-30T10:00:00",
            "last_applied_at": "2026-06-30T10:00:00",
        },
    ]
    result = assemble_learned_context(None, rules)
    assert result is not None
    high_pos = result.index("High confidence rule")
    low_pos = result.index("Low confidence rule")
    assert high_pos < low_pos


def test_rules_with_evidence():
    rules = [
        {
            "id": 1,
            "repo": "org/repo",
            "rule_text": "Skip perf findings",
            "evidence_count": 7,
            "confidence": 0.8,
            "created_at": "2026-06-30T10:00:00",
            "last_applied_at": "2026-06-30T10:00:00",
        }
    ]
    result = assemble_learned_context(None, rules)
    assert result is not None
    assert "7 dismissal(s)" in result


def test_with_created_at_sort_break_ties():
    rules = [
        {
            "id": 1,
            "repo": "org/repo",
            "rule_text": "Older rule",
            "evidence_count": 3,
            "confidence": 0.8,
            "created_at": "2026-06-28T10:00:00",
            "last_applied_at": "2026-06-28T10:00:00",
        },
        {
            "id": 2,
            "repo": "org/repo",
            "rule_text": "Newer rule",
            "evidence_count": 3,
            "confidence": 0.8,
            "created_at": "2026-06-30T10:00:00",
            "last_applied_at": "2026-06-30T10:00:00",
        },
    ]
    result = assemble_learned_context(None, rules)
    assert result is not None
    newer_pos = result.index("Newer rule")
    older_pos = result.index("Older rule")
    assert newer_pos < older_pos
