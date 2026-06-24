from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from superseded.models import Finding, ReviewResult
from superseded.output.github_pr import post_review_to_pr
from superseded.output.json_out import format_json
from superseded.output.markdown import format_markdown
from superseded.output.table import format_table


def make_result():
    return ReviewResult(
        findings=[
            Finding(
                pass_name="security",
                severity="critical",
                file="src/auth.py",
                line=42,
                end_line=45,
                title="SQL injection",
                description="User input in SQL",
                suggestion="Use params",
            ),
        ]
    )


def make_important_result():
    return ReviewResult(
        findings=[
            Finding(
                pass_name="correctness",
                severity="important",
                file="src/api.py",
                line=10,
                end_line=12,
                title="Off-by-one",
                description="desc",
                suggestion="fix",
            ),
        ]
    )


def test_json_output():
    result = make_result()
    out = format_json(result)
    data = json.loads(out)
    assert len(data) == 1
    assert data[0]["severity"] == "critical"


def test_json_output_includes_id():
    result = make_result()
    out = format_json(result)
    data = json.loads(out)
    assert "id" in data[0]
    assert data[0]["id"].startswith("security-")


def test_markdown_output():
    result = make_result()
    out = format_markdown(result)
    assert "# Code Review" in out
    assert "critical" in out.lower()
    assert "SQL injection" in out


def test_table_output():
    result = make_result()
    out = format_table(result)
    assert "critical" in out
    assert "SQL injection" in out


def test_empty_result():
    result = ReviewResult(findings=[])
    assert "No issues" in format_markdown(result).lower() or format_markdown(result).strip() != ""
    assert "No issues" in format_table(result).lower() or format_table(result).strip() != ""


@patch("subprocess.run")
def test_post_review_to_pr(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    result = make_result()
    post_review_to_pr(pr=123, result=result, repo="owner/repo")
    # Should call gh api to create a review
    assert mock_run.called
    args = mock_run.call_args[0][0]
    assert "gh" in args
    assert "api" in args


@patch("subprocess.run")
def test_post_review_includes_pass_labels(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    result = make_result()
    post_review_to_pr(pr=123, result=result, repo="owner/repo")
    # Check the JSON payload passed via stdin
    call_kwargs = mock_run.call_args[1]
    payload = json.loads(call_kwargs["input"])
    assert "Security Review" in payload["body"]


@patch("subprocess.run")
def test_post_review_important_triggers_request_changes(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    result = make_important_result()
    post_review_to_pr(pr=123, result=result, repo="owner/repo")
    call_kwargs = mock_run.call_args[1]
    payload = json.loads(call_kwargs["input"])
    assert payload["event"] == "REQUEST_CHANGES"


@patch("subprocess.run")
def test_post_review_suggestion_is_comment(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    result = ReviewResult(
        findings=[
            Finding(
                pass_name="style",
                severity="suggestion",
                file="x.py",
                line=1,
                end_line=1,
                title="naming",
                description="d",
                suggestion="s",
            )
        ]
    )
    post_review_to_pr(pr=123, result=result, repo="owner/repo")
    payload = json.loads(mock_run.call_args[1]["input"])
    assert payload["event"] == "COMMENT"


@patch("subprocess.run")
def test_post_review_returns_comment_ids(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout='{"id": 777, "comments": [{"id": 9001}, {"id": 9002}]}',
        stderr="",
    )
    result = make_result()
    ids = post_review_to_pr(pr=123, result=result, repo="owner/repo")
    assert ids == [9001, 9002]


@patch("subprocess.run")
def test_post_review_multiline_finding_has_start_line(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout='{"id": 1, "comments": []}', stderr="")
    result = ReviewResult(
        findings=[
            Finding(
                pass_name="security",
                severity="critical",
                file="auth.py",
                line=40,
                end_line=50,
                title="multi",
                description="d",
                suggestion="s",
            )
        ]
    )
    post_review_to_pr(pr=1, result=result, repo="owner/repo")
    payload = json.loads(mock_run.call_args[1]["input"])
    comment = payload["comments"][0]
    assert comment["start_line"] == 40
    assert comment["line"] == 50


@patch("subprocess.run")
def test_post_review_single_line_finding_has_no_start_line(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout='{"id": 1, "comments": []}', stderr="")
    result = ReviewResult(
        findings=[
            Finding(
                pass_name="style",
                severity="nit",
                file="x.py",
                line=5,
                end_line=5,
                title="naming",
                description="d",
                suggestion="s",
            )
        ]
    )
    post_review_to_pr(pr=1, result=result, repo="owner/repo")
    payload = json.loads(mock_run.call_args[1]["input"])
    assert "start_line" not in payload["comments"][0]


def _finding(**overrides):
    defaults = dict(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        end_line=2,
        title="bad",
        description="desc",
        suggestion="fix",
    )
    defaults.update(overrides)
    return Finding(**defaults)


def test_reasoning_renders_when_present():
    f = _finding(reasoning="Suspicious input from user request")
    result = format_markdown(ReviewResult(findings=[f]))
    assert "<details>" in result
    assert "Suspicious input from user request" in result
    assert "Reasoning" in result


def test_reasoning_absent_when_empty():
    f = _finding(reasoning="")
    result = format_markdown(ReviewResult(findings=[f]))
    assert "<details>" not in result


def test_reasoning_in_correct_position():
    f = _finding(reasoning="because X")
    result = format_markdown(ReviewResult(findings=[f]))
    desc_pos = result.index("desc")
    details_pos = result.index("<details>")
    suggestion_pos = result.index("**Suggestion:**")
    assert desc_pos < details_pos < suggestion_pos
