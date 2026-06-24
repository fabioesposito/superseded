from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from superseded.cli import cli
from superseded.models import Finding, ReviewResult


class FakeStore:
    def __init__(self):
        self.findings = {}
        self.comment_ids = {}
        self.feedback = []
        self._dismissed = set()
        self.dismissed_calls = 0

    async def init(self):
        pass

    async def get_dismissed_findings(self, repo):
        self.dismissed_calls += 1
        return [self.findings[fid] for fid in self._dismissed if self.findings[fid]["repo"] == repo]

    async def record_finding(
        self, finding_id, repo, pass_name, severity, file, line, title, description
    ):
        self.findings[finding_id] = {
            "id": finding_id,
            "repo": repo,
            "pass": pass_name,
            "severity": severity,
            "file": file,
            "line": line,
            "title": title,
            "description": description,
        }

    async def set_comment_id(self, finding_id, comment_id):
        self.comment_ids[comment_id] = finding_id
        if finding_id in self.findings:
            self.findings[finding_id]["comment_id"] = comment_id

    async def record_feedback_by_comment_id(self, comment_id, action):
        fid = self.comment_ids.get(comment_id)
        if fid is None:
            return False
        self.feedback.append((fid, action))
        if action == "dismiss":
            self._dismissed.add(fid)
        return True


def _make_finding():
    return Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        end_line=2,
        title="t",
        description="d",
        suggestion="s",
    )


@patch("superseded.cli.post_review_to_pr")
@patch("superseded.cli.MemoryStore")
@patch("superseded.cli.current_repo")
@patch("superseded.cli.ReviewEngine")
@patch("superseded.cli.compute_file_context")
@patch("superseded.cli.fetch_pr_description")
@patch("superseded.cli.fetch_diff")
def test_review_wires_memory_persistence_and_comment_ids(
    mock_fetch, mock_desc, mock_ctx, mock_engine_cls, mock_repo, mock_store_cls, mock_post
):
    mock_fetch.return_value = "diff"
    mock_ctx.return_value = "ctx"
    mock_desc.return_value = "PR body"
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    finding = _make_finding()
    mock_engine.review.return_value = ReviewResult(findings=[finding])
    mock_repo.return_value = "owner/repo"
    store = FakeStore()
    mock_store_cls.return_value = store
    mock_post.return_value = [9001]

    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--pr", "123", "--post"])

    assert result.exit_code == 0, result.output
    assert mock_engine.review.call_count == 1
    kwargs = mock_engine.review.call_args.kwargs
    assert kwargs.get("pr_description") == "PR body"
    assert kwargs.get("file_context") == "ctx"
    assert store.dismissed_calls >= 1
    assert finding.id in store.findings
    assert store.comment_ids.get(9001) == finding.id
    mock_post.assert_called_once()


@patch("superseded.cli.MemoryStore")
@patch("superseded.cli.current_repo")
@patch("superseded.cli.ReviewEngine")
@patch("superseded.cli.fetch_diff")
def test_review_injects_dismissed_findings_as_memory(
    mock_fetch, mock_engine_cls, mock_repo, mock_store_cls
):
    mock_fetch.return_value = "diff"
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    mock_engine.review.return_value = ReviewResult(findings=[])
    mock_repo.return_value = "owner/repo"
    store = FakeStore()
    store.findings["security-old"] = {
        "id": "security-old",
        "repo": "owner/repo",
        "pass": "security",
        "severity": "suggestion",
        "file": "old.py",
        "line": 9,
        "title": "Missing type hints",
        "description": "dismissed before",
    }
    store.comment_ids[5] = "security-old"
    store._dismissed.add("security-old")
    mock_store_cls.return_value = store

    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--pr", "1"])

    assert result.exit_code == 0, result.output
    kwargs = mock_engine.review.call_args.kwargs
    mem = kwargs.get("memory_context")
    assert mem and "Missing type hints" in mem


@patch("superseded.cli.fetch_diff")
@patch("superseded.cli.ReviewEngine")
@patch("superseded.cli.compute_file_context")
@patch("superseded.cli.fetch_pr_description")
def test_review_passes_context_args_even_without_memory(
    mock_desc, mock_ctx, mock_engine_cls, mock_fetch
):
    mock_fetch.return_value = "diff"
    mock_ctx.return_value = "ctx"
    mock_desc.return_value = "PR body"
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    mock_engine.review.return_value = ReviewResult(findings=[])

    with patch("superseded.cli.current_repo", return_value=None):
        runner = CliRunner()
        result = runner.invoke(cli, ["review", "--pr", "5"])

    assert result.exit_code == 0, result.output
    kwargs = mock_engine.review.call_args.kwargs
    assert kwargs.get("file_context") == "ctx"
    assert kwargs.get("pr_description") == "PR body"


@patch("superseded.cli.check_pr_feedback")
def test_feedback_check_records_reactions_as_dismiss(mock_check):
    store = FakeStore()
    finding = _make_finding()
    asyncio.run(
        store.record_finding(finding.id, "owner/repo", "security", "critical", "a.py", 1, "t", "d")
    )
    asyncio.run(store.set_comment_id(finding.id, 42))
    mock_check.return_value = [
        {"id": 42, "path": "a.py", "line": 1, "reactions": {"+1": 0, "-1": 2}, "resolved": False},
        {"id": 99, "path": "x.py", "line": 1, "reactions": {"+1": 3, "-1": 0}, "resolved": False},
    ]

    with (
        patch("superseded.cli.MemoryStore", return_value=store),
        patch("superseded.cli.current_repo", return_value="owner/repo"),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["feedback", "--check", "--pr", "1"])

    assert result.exit_code == 0, result.output
    assert (finding.id, "dismiss") in store.feedback


@patch("superseded.cli.check_pr_feedback")
def test_feedback_check_records_resolved_as_dismiss(mock_check):
    store = FakeStore()
    finding = _make_finding()
    asyncio.run(
        store.record_finding(finding.id, "owner/repo", "security", "critical", "a.py", 1, "t", "d")
    )
    asyncio.run(store.set_comment_id(finding.id, 7))
    mock_check.return_value = [{"id": 7, "resolved": True, "reactions": {"+1": 0, "-1": 0}}]

    with (
        patch("superseded.cli.MemoryStore", return_value=store),
        patch("superseded.cli.current_repo", return_value="owner/repo"),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["feedback", "--check", "--pr", "1"])

    assert result.exit_code == 0, result.output
    assert (finding.id, "dismiss") in store.feedback


def test_feedback_manual_dismiss_resolves_comment_id():
    store = FakeStore()
    finding = _make_finding()
    asyncio.run(
        store.record_finding(finding.id, "owner/repo", "security", "critical", "a.py", 1, "t", "d")
    )
    asyncio.run(store.set_comment_id(finding.id, 555))

    with patch("superseded.cli.MemoryStore", return_value=store):
        runner = CliRunner()
        result = runner.invoke(cli, ["feedback", "555", "--dismiss"])

    assert result.exit_code == 0, result.output
    assert (finding.id, "dismiss") in store.feedback


def test_feedback_manual_unknown_comment_id_reports_error():
    store = FakeStore()
    with patch("superseded.cli.MemoryStore", return_value=store):
        runner = CliRunner()
        result = runner.invoke(cli, ["feedback", "999", "--dismiss"])
    assert result.exit_code != 0
    assert "999" in result.output
