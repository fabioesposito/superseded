from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

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


def test_reasoning_html_escaped_in_markdown():
    f = _finding(reasoning="looks like </details><details open> injection")
    result = format_markdown(ReviewResult(findings=[f]))
    assert "&lt;/details&gt;" in result
    assert "</details><details open>" not in result
    assert result.count("<details>") == 1
    assert result.count("</details>") == 1


def test_pr_comment_reasoning_html_escaped():
    f = _finding(reasoning="looks like </details> break")
    result = ReviewResult(findings=[f])
    payloads = []

    def fake_run(cmd, **kwargs):
        payloads.append(json.loads(kwargs.get("input", "{}")))
        return MagicMock(
            returncode=0,
            stdout=json.dumps({"comments": [{"id": 1}]}),
            stderr="",
        )

    with (
        patch("superseded.output.github_pr.subprocess.run", side_effect=fake_run),
        patch("superseded.output.github_pr._repo", return_value="owner/repo"),
    ):
        post_review_to_pr(pr=1, result=result)

    body = payloads[0]["comments"][0]["body"]
    assert "&lt;/details&gt;" in body
    assert "</details> break" not in body
    assert body.count("<details>") == 1
    assert body.count("</details>") == 1


def test_pr_comment_includes_reasoning_when_present():
    f = _finding(reasoning="Suspicious pattern detected")
    result = ReviewResult(findings=[f])
    payloads = []

    def fake_run(cmd, **kwargs):
        payloads.append(json.loads(kwargs.get("input", "{}")))
        return MagicMock(
            returncode=0,
            stdout=json.dumps({"comments": [{"id": 1}]}),
            stderr="",
        )

    with (
        patch("superseded.output.github_pr.subprocess.run", side_effect=fake_run),
        patch("superseded.output.github_pr._repo", return_value="owner/repo"),
    ):
        post_review_to_pr(pr=1, result=result)

    assert payloads
    body = payloads[0]["comments"][0]["body"]
    assert "<details>" in body
    assert "Suspicious pattern detected" in body


def test_pr_comment_excludes_reasoning_when_empty():
    f = _finding(reasoning="")
    result = ReviewResult(findings=[f])
    payloads = []

    def fake_run(cmd, **kwargs):
        payloads.append(json.loads(kwargs.get("input", "{}")))
        return MagicMock(
            returncode=0,
            stdout=json.dumps({"comments": [{"id": 1}]}),
            stderr="",
        )

    with (
        patch("superseded.output.github_pr.subprocess.run", side_effect=fake_run),
        patch("superseded.output.github_pr._repo", return_value="owner/repo"),
    ):
        post_review_to_pr(pr=1, result=result)

    body = payloads[0]["comments"][0]["body"]
    assert "<details>" not in body


def test_current_repo_returns_none_on_called_process_error():
    with patch(
        "superseded.output.github_pr._repo", side_effect=subprocess.CalledProcessError(1, "gh")
    ):
        from superseded.output.github_pr import current_repo

        assert current_repo() is None


def test_current_repo_returns_none_on_file_not_found():
    with patch("superseded.output.github_pr._repo", side_effect=FileNotFoundError):
        from superseded.output.github_pr import current_repo

        assert current_repo() is None


def test_partition_comments_all_valid():
    from superseded.output.github_pr import _partition_comments

    payload = {"body": "test", "comments": [{"path": "a.py", "line": 1, "body": "x"}]}
    with patch(
        "superseded.output.github_pr.subprocess.run",
        return_value=MagicMock(
            returncode=0,
            stdout=json.dumps({"comments": [{"id": 1}]}),
            stderr="",
        ),
    ):
        bad, ids = _partition_comments(payload["comments"], payload, "r", 1)
        assert bad == set()
        assert ids == [1]


def test_partition_comments_one_bad():
    from superseded.output.github_pr import _partition_comments

    payload = {"body": "test", "comments": [{"path": "gone.py", "line": 999, "body": "x"}]}
    with patch(
        "superseded.output.github_pr.subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "gh"),
    ):
        bad, ids = _partition_comments(payload["comments"], payload, "r", 1)
        assert bad == {0}
        assert ids == []


def test_partition_comments_mixed():
    from superseded.output.github_pr import _partition_comments

    payload = {
        "body": "test",
        "comments": [
            {"path": "a.py", "line": 1, "body": "good"},
            {"path": "b.py", "line": 999, "body": "bad"},
        ],
    }

    def side_effect(cmd, **kwargs):
        input_json = json.loads(kwargs.get("input", "{}"))
        comment_lines = [c["line"] for c in input_json.get("comments", [])]
        if 999 in comment_lines:
            raise subprocess.CalledProcessError(1, "gh")
        return MagicMock(
            returncode=0,
            stdout=json.dumps({"comments": [{"id": 1}]}),
            stderr="",
        )

    with patch("superseded.output.github_pr.subprocess.run", side_effect=side_effect):
        bad, ids = _partition_comments(payload["comments"], payload, "r", 1)
        assert bad == {1}
        assert len(ids) == 1


def test_partition_comments_empty():
    from superseded.output.github_pr import _partition_comments

    bad, ids = _partition_comments([], {"body": "test"}, "r", 1)
    assert bad == set()
    assert ids == []


def test_partition_comments_all_valid_three():
    from superseded.output.github_pr import _partition_comments

    payload = {
        "body": "test",
        "comments": [
            {"path": "a.py", "line": 1, "body": "c1"},
            {"path": "b.py", "line": 2, "body": "c2"},
            {"path": "c.py", "line": 3, "body": "c3"},
        ],
    }

    with patch(
        "superseded.output.github_pr.subprocess.run",
        return_value=MagicMock(
            returncode=0,
            stdout=json.dumps({"comments": [{"id": 1}, {"id": 2}, {"id": 3}]}),
            stderr="",
        ),
    ):
        bad, ids = _partition_comments(payload["comments"], payload, "r", 1)
        assert bad == set()
        assert len(ids) == 3


def test_partition_comments_one_bad_three():
    from superseded.output.github_pr import _partition_comments

    payload = {
        "body": "test",
        "comments": [
            {"path": "a.py", "line": 1, "body": "c1"},
            {"path": "b.py", "line": 999, "body": "c2"},
            {"path": "c.py", "line": 3, "body": "c3"},
        ],
    }

    def side_effect(cmd, **kwargs):
        input_json = json.loads(kwargs.get("input", "{}"))
        comment_lines = [c["line"] for c in input_json.get("comments", [])]
        if 999 in comment_lines:
            raise subprocess.CalledProcessError(1, "gh")
        return MagicMock(
            returncode=0,
            stdout=json.dumps({"comments": [{"id": 1}]}),
            stderr="",
        )

    with patch("superseded.output.github_pr.subprocess.run", side_effect=side_effect):
        bad, ids = _partition_comments(payload["comments"], payload, "r", 1)
        assert bad == {1}
        assert len(ids) == 2


def test_build_fallback_text_single():
    from superseded.output.github_pr import _build_fallback_text

    f = Finding(
        pass_name="security",
        severity="critical",
        file="src/auth.py",
        line=142,
        end_line=142,
        title="SQL injection",
        description="User input in SQL",
        suggestion="Use params",
    )
    text = _build_fallback_text([f])
    assert "## Out-of-range findings" in text
    assert "src/auth.py:142" in text
    assert "[critical]" in text
    assert "SQL injection" in text


def test_build_fallback_text_multiple():
    from superseded.output.github_pr import _build_fallback_text

    f1 = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        end_line=1,
        title="t1",
        description="d",
        suggestion="s",
    )
    f2 = Finding(
        pass_name="style",
        severity="nit",
        file="b.py",
        line=2,
        end_line=2,
        title="t2",
        description="d",
        suggestion="s",
    )
    text = _build_fallback_text([f1, f2])
    assert text.count("- **") == 2
    assert "a.py:1" in text
    assert "b.py:2" in text


def test_partition_comments_two_bad_three():
    from superseded.output.github_pr import _partition_comments

    payload = {
        "body": "test",
        "comments": [
            {"path": "a.py", "line": 1, "body": "c1"},
            {"path": "b.py", "line": 999, "body": "c2"},
            {"path": "c.py", "line": 999, "body": "c3"},
        ],
    }

    def side_effect(cmd, **kwargs):
        input_json = json.loads(kwargs.get("input", "{}"))
        comment_lines = [c["line"] for c in input_json.get("comments", [])]
        if 999 in comment_lines:
            raise subprocess.CalledProcessError(1, "gh")
        return MagicMock(
            returncode=0,
            stdout=json.dumps({"comments": [{"id": 1}]}),
            stderr="",
        )

    with patch("superseded.output.github_pr.subprocess.run", side_effect=side_effect):
        bad, ids = _partition_comments(payload["comments"], payload, "r", 1)
        assert bad == {1, 2}
        assert len(ids) == 1


def test_post_review_fallback_mixed():
    """Happy path fails, binary search isolates bad comment, final post is body-only."""
    result = ReviewResult(
        findings=[
            Finding(
                pass_name="security",
                severity="critical",
                file="ok.py",
                line=1,
                end_line=1,
                title="good",
                description="d",
                suggestion="s",
            ),
            Finding(
                pass_name="style",
                severity="nit",
                file="bad.py",
                line=999,
                end_line=999,
                title="out-of-range",
                description="d",
                suggestion="s",
            ),
        ]
    )

    call_count = [0]
    payloads = []

    def side_effect(cmd, **kwargs):
        call_count[0] += 1
        input_json = json.loads(kwargs.get("input", "{}"))
        payloads.append(input_json)
        if call_count[0] == 1:
            raise subprocess.CalledProcessError(1, "gh")
        comment_bodies = [c.get("body", "") for c in input_json.get("comments", [])]
        if any("out-of-range" in b for b in comment_bodies):
            raise subprocess.CalledProcessError(1, "gh")
        return MagicMock(
            returncode=0,
            stdout=json.dumps({"comments": [{"id": 1}]}),
            stderr="",
        )

    with (
        patch("superseded.output.github_pr.subprocess.run", side_effect=side_effect),
        patch("superseded.output.github_pr._repo", return_value="owner/repo"),
    ):
        ids = post_review_to_pr(pr=1, result=result)

    # Final payload is body-only (valid comments already live from probe)
    final_payload = payloads[-1]
    assert final_payload["comments"] == []
    assert "## Out-of-range findings" in final_payload["body"]
    assert "bad.py:999" in final_payload["body"]
    assert ids == [1, None]  # valid finding gets ID, out-of-range gets None


def test_post_review_fallback_all_bad():
    """All comments out of range -> body-only review with fallback text."""
    result = ReviewResult(
        findings=[
            Finding(
                pass_name="security",
                severity="critical",
                file="a.py",
                line=999,
                end_line=999,
                title="bad1",
                description="d",
                suggestion="s",
            ),
            Finding(
                pass_name="style",
                severity="nit",
                file="b.py",
                line=999,
                end_line=999,
                title="bad2",
                description="d",
                suggestion="s",
            ),
        ]
    )

    payloads = []

    def side_effect(cmd, **kwargs):
        input_json = json.loads(kwargs.get("input", "{}"))
        payloads.append(input_json)
        if input_json.get("comments"):
            raise subprocess.CalledProcessError(1, "gh")
        return MagicMock(
            returncode=0,
            stdout=json.dumps({"id": 1, "comments": []}),
            stderr="",
        )

    with (
        patch("superseded.output.github_pr.subprocess.run", side_effect=side_effect),
        patch("superseded.output.github_pr._repo", return_value="owner/repo"),
    ):
        ids = post_review_to_pr(pr=1, result=result)

    final_payload = payloads[-1]
    assert final_payload["comments"] == []
    assert "## Out-of-range findings" in final_payload["body"]
    assert "a.py:999" in final_payload["body"]
    assert "b.py:999" in final_payload["body"]
    assert ids == [None, None]  # all out-of-range, no valid IDs


def test_post_review_no_comments_raises():
    """CalledProcessError with empty comments re-raises."""
    result = ReviewResult(findings=[])

    with (
        patch(
            "superseded.output.github_pr.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "gh"),
        ),
        patch("superseded.output.github_pr._repo", return_value="owner/repo"),
        pytest.raises(subprocess.CalledProcessError),
    ):
        post_review_to_pr(pr=1, result=result)
