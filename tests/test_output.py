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


def test_json_output():
    result = make_result()
    out = format_json(result)
    data = json.loads(out)
    assert len(data) == 1
    assert data[0]["severity"] == "critical"


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
