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
        stdout='{"id": 777, "comments": [{"id": 9001}]}',
        stderr="",
    )
    result = make_result()
    ids = post_review_to_pr(pr=123, result=result, repo="owner/repo", diff="")
    assert ids == [9001]


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


def test_review_payload_redacts_secrets_in_finding_fields():
    from superseded.output.github_pr import build_review_payload

    result = ReviewResult(
        findings=[
            Finding(
                pass_name="security",
                severity="critical",
                file="src/auth.py",
                line=1,
                end_line=1,
                title="Leaked key ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789 here",
                description="AWS key AKIAIOSFODNN7EXAMPLE and sk-ant-api03-abcdef1234567890abcd seen",
                suggestion="rotate the Bearer abcdefghijklmnop0123456789 token",
            )
        ]
    )
    body = build_review_payload(result)["comments"][0]["body"]
    assert "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789" not in body
    assert "AKIAIOSFODNN7EXAMPLE" not in body
    assert "sk-ant-api03-abcdef1234567890abcd" not in body
    assert "Bearer abcdefghijklmnop0123456789" not in body
    assert body.count("[REDACTED]") >= 3


def test_review_payload_truncates_oversized_comment_body():
    from superseded.output.github_pr import MAX_COMMENT_CHARS, build_review_payload

    result = ReviewResult(
        findings=[
            Finding(
                pass_name="security",
                severity="critical",
                file="a.py",
                line=1,
                end_line=1,
                title="t",
                description="x" * (MAX_COMMENT_CHARS + 500),
                suggestion="s",
            )
        ]
    )
    body = build_review_payload(result)["comments"][0]["body"]
    assert len(body) <= MAX_COMMENT_CHARS + 50
    assert "comment truncated" in body


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


def test_out_of_range_indices_matches_hunks():
    from superseded.output.github_pr import _out_of_range_indices

    diff = """diff --git a/ok.py b/ok.py
--- a/ok.py
+++ b/ok.py
@@ -1,5 +1,5 @@
 context
-old
+new
 context
diff --git a/other.py b/other.py
--- a/other.py
+++ b/other.py
@@ -10,3 +10,3 @@
 ctx
-old
+new
"""
    comments = [
        {"path": "ok.py", "line": 3},  # in hunk (1..5)
        {"path": "ok.py", "line": 99},  # outside hunk
        {"path": "missing.py", "line": 1},  # file absent from diff
        {"path": "other.py", "line": 10},  # in other hunk (10..12)
    ]
    bad = _out_of_range_indices(comments, diff)
    assert bad == {1, 2}


def test_out_of_range_indices_empty_diff_treats_all_as_good():
    from superseded.output.github_pr import _out_of_range_indices

    comments = [{"path": "a.py", "line": 999}]
    assert _out_of_range_indices(comments, "") == set()
    assert _out_of_range_indices(comments, "   ") == set()


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


def test_post_review_local_validation_mixed():
    """In-range findings post once; out-of-range move to a single body-only fallback."""
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

    # Diff has a hunk only for ok.py, line 1 is in range.
    diff = """diff --git a/ok.py b/ok.py
--- a/ok.py
+++ b/ok.py
@@ -1,3 +1,3 @@
 ctx
-old
+new
 ctx
"""
    payloads = []
    call_count = [0]

    def side_effect(cmd, **kwargs):
        call_count[0] += 1
        input_json = json.loads(kwargs.get("input", "{}"))
        payloads.append(input_json)
        # No probe posts — patches on real GH would not be expected to fail here.
        return MagicMock(
            returncode=0,
            stdout=json.dumps({"comments": [{"id": 7}]}),
            stderr="",
        )

    with (
        patch("superseded.output.github_pr.subprocess.run", side_effect=side_effect),
        patch("superseded.output.github_pr._repo", return_value="owner/repo"),
    ):
        ids = post_review_to_pr(pr=1, result=result, diff=diff)

    # Exactly two posts: good-comments review, then body-only fallback.
    assert call_count[0] == 2
    first, second = payloads
    # First post carries only the in-range comment.
    assert len(first["comments"]) == 1
    assert first["comments"][0]["path"] == "ok.py"
    # Second post is body-only with fallback section appended.
    assert second["comments"] == []
    assert "## Out-of-range findings" in second["body"]
    assert "bad.py:999" in second["body"]
    assert ids == [7, None]


def test_post_review_local_validation_all_bad():
    """When every finding is out of range, only ONE body-only post happens."""
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
    # Diff touches a.py but only around line 1, so 999 is out of range; b.py absent.
    diff = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,1 +1,1 @@
-old
+new
"""
    payloads = []

    def side_effect(cmd, **kwargs):
        input_json = json.loads(kwargs.get("input", "{}"))
        payloads.append(input_json)
        return MagicMock(
            returncode=0,
            stdout=json.dumps({"id": 1, "comments": []}),
            stderr="",
        )

    with (
        patch("superseded.output.github_pr.subprocess.run", side_effect=side_effect),
        patch("superseded.output.github_pr._repo", return_value="owner/repo"),
    ):
        ids = post_review_to_pr(pr=1, result=result, diff=diff)

    assert len(payloads) == 1  # no probe posts — single body-only review
    assert payloads[0]["comments"] == []
    assert "a.py:999" in payloads[0]["body"]
    assert "b.py:999" in payloads[0]["body"]
    assert ids == [None, None]


def test_post_review_local_validation_rejects_probe_posts():
    """Local validation must never fall back to binary-search probe spam.

    Even if GitHub rejects the pre-filtered good batch (e.g. stale diff), the
    recovery is a single body-only review — not recursive probe posts.
    """
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
    diff = """diff --git a/ok.py b/ok.py
--- a/ok.py
+++ b/ok.py
@@ -1,3 +1,3 @@
 ctx
-old
+new
 ctx
"""
    call_count = [0]

    def side_effect(cmd, **kwargs):
        call_count[0] += 1
        input_json = json.loads(kwargs.get("input", "{}"))
        # Every post carrying inline comments fails (simulating stale diff).
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
        ids = post_review_to_pr(pr=1, result=result, diff=diff)

    # Exactly two posts total: the rejected good batch, then one body-only.
    assert call_count[0] == 2
    assert ids == [None, None]


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
        post_review_to_pr(pr=1, result=result, diff="")
