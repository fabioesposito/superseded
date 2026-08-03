"""Tests for review.verifier._parse_verdicts()."""

from __future__ import annotations

import json

from superseded.review.verifier import _parse_verdicts


def test_parse_verdicts_keep():
    raw = json.dumps(
        [
            {
                "id": "sec-abc123",
                "action": "keep",
                "severity": "suggestion",
                "confidence": "low",
                "reason": "valid",
            }
        ]
    )
    verdicts = _parse_verdicts(raw)
    assert len(verdicts) == 1
    assert verdicts["sec-abc123"].action == "keep"
    assert verdicts["sec-abc123"].severity == "suggestion"
    assert verdicts["sec-abc123"].confidence == "low"
    assert verdicts["sec-abc123"].reason == "valid"


def test_parse_verdicts_drop():
    raw = json.dumps([{"id": "cor-xyz789", "action": "drop", "reason": "already handled"}])
    verdicts = _parse_verdicts(raw)
    assert verdicts["cor-xyz789"].action == "drop"
    assert verdicts["cor-xyz789"].reason == "already handled"


def test_parse_verdicts_keep_with_no_reestimate():
    raw = json.dumps([{"id": "perf-111", "action": "keep", "reason": "looks right"}])
    verdicts = _parse_verdicts(raw)
    assert verdicts["perf-111"].action == "keep"
    assert verdicts["perf-111"].severity is None
    assert verdicts["perf-111"].confidence is None


def test_parse_verdicts_invalid_action_skipped():
    raw = json.dumps([{"id": "x", "action": "something_else", "reason": "hmm"}])
    errors, verdicts = _parse_verdicts(raw, collect_errors=True)
    assert len(verdicts) == 0
    assert len(errors) == 1


def test_parse_verdicts_invalid_severity_skipped():
    raw = json.dumps([{"id": "x", "action": "keep", "severity": "extreme", "reason": "bad"}])
    errors, verdicts = _parse_verdicts(raw, collect_errors=True)
    assert len(verdicts) == 1
    assert verdicts["x"].severity is None
    assert len(errors) == 0


def test_parse_verdicts_non_json_returns_empty():
    raw = "This is not JSON at all"
    errors, verdicts = _parse_verdicts(raw, collect_errors=True)
    assert len(verdicts) == 0
    assert len(errors) == 1


def test_parse_verdicts_empty_array():
    raw = "[]"
    errors, verdicts = _parse_verdicts(raw, collect_errors=True)
    assert len(verdicts) == 0
    assert len(errors) == 0


def test_parse_verdicts_duplicate_ids_keeps_last():
    raw = json.dumps(
        [
            {"id": "dup", "action": "keep", "reason": "first"},
            {"id": "dup", "action": "drop", "reason": "second"},
        ]
    )
    verdicts = _parse_verdicts(raw)
    assert verdicts["dup"].action == "drop"
