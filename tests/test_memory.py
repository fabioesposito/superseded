from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from superseded.memory.feedback import check_pr_feedback, check_resolved_threads
from superseded.memory.store import MemoryStore


async def _test_store():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = MemoryStore(db_path)
        await store.init()

        await store.record_finding(
            finding_id="sec-abc123",
            repo="owner/repo",
            pass_name="security",
            severity="critical",
            file="a.py",
            line=42,
            title="SQL injection",
            description="desc",
        )

        findings = await store.get_dismissed_findings("owner/repo")
        assert len(findings) == 0  # not dismissed yet

        await store.record_feedback("sec-abc123", "dismiss")
        findings = await store.get_dismissed_findings("owner/repo")
        assert len(findings) == 1
        assert findings[0]["id"] == "sec-abc123"


def test_memory_store():
    asyncio.run(_test_store())


async def _test_comment_id_mapping():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = MemoryStore(db_path)
        await store.init()
        await store.record_finding(
            finding_id="sec-abc123",
            repo="owner/repo",
            pass_name="security",
            severity="critical",
            file="a.py",
            line=42,
            title="SQL injection",
            description="desc",
        )
        await store.set_comment_id("sec-abc123", 9001)
        found = await store.get_finding_by_comment_id(9001)
        assert found is not None
        assert found["id"] == "sec-abc123"
        assert found["comment_id"] == 9001
        assert await store.get_finding_by_comment_id(9999) is None


def test_comment_id_mapping():
    asyncio.run(_test_comment_id_mapping())


async def _test_dismissed_by_comment_id():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = MemoryStore(db_path)
        await store.init()
        await store.record_finding(
            finding_id="sec-xyz",
            repo="owner/repo",
            pass_name="security",
            severity="critical",
            file="a.py",
            line=1,
            title="t",
            description="d",
        )
        await store.set_comment_id("sec-xyz", 42)
        await store.record_feedback_by_comment_id(42, "dismiss")
        findings = await store.get_dismissed_findings("owner/repo")
        assert len(findings) == 1
        assert findings[0]["id"] == "sec-xyz"


def test_dismissed_by_comment_id():
    asyncio.run(_test_dismissed_by_comment_id())


@patch("superseded.memory.feedback.check_resolved_threads")
@patch("subprocess.run")
def test_check_pr_feedback_returns_reactions_and_resolution(mock_run, mock_resolved):
    mock_resolved.return_value = set()
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=(
            '{"id": 1, "body": "test", "path": "a.py", "line": 1, "reactions": {"+1": 0, "-1": 2}}\n'
            '{"id": 2, "body": "good", "path": "b.py", "line": 2, "reactions": {"+1": 3, "-1": 0}}\n'
        ),
    )
    feedback = check_pr_feedback(pr=123, repo="owner/repo")
    assert len(feedback) == 2
    assert isinstance(feedback[0], dict)
    assert feedback[0]["id"] == 1
    assert feedback[0]["reactions"]["-1"] == 2
    assert feedback[1]["reactions"]["+1"] == 3


@patch("superseded.memory.feedback.check_resolved_threads")
@patch("subprocess.run")
def test_check_pr_feedback_empty(mock_run, mock_resolved):
    mock_resolved.return_value = set()
    mock_run.return_value = MagicMock(returncode=0, stdout="")
    assert check_pr_feedback(pr=123, repo="owner/repo") == []


@patch("superseded.memory.feedback.check_resolved_threads")
@patch("subprocess.run")
def test_check_pr_feedback_jq_uses_top_level_line(mock_run, mock_resolved):
    mock_resolved.return_value = set()
    mock_run.return_value = MagicMock(returncode=0, stdout="")
    check_pr_feedback(pr=123, repo="owner/repo")
    cmd = mock_run.call_args.args[0]
    jq_expr = cmd[cmd.index("--jq") + 1]
    assert "..line" not in jq_expr
    assert "line: .line" in jq_expr
    assert "_resolved" not in jq_expr


@patch("subprocess.run")
def test_check_resolved_threads_empty(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [],
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            }
                        }
                    }
                }
            }
        ),
    )
    resolved = check_resolved_threads(pr=123, owner="o", repo="r")
    assert resolved == set()


@patch("subprocess.run")
def test_check_resolved_threads_finds_resolved(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [
                                    {
                                        "isResolved": True,
                                        "comments": {"nodes": [{"databaseId": 9001}]},
                                    },
                                    {
                                        "isResolved": False,
                                        "comments": {"nodes": [{"databaseId": 9002}]},
                                    },
                                ],
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            }
                        }
                    }
                }
            }
        ),
    )
    resolved = check_resolved_threads(pr=1, owner="o", repo="r")
    assert resolved == {9001}


@patch("subprocess.run")
def test_check_resolved_threads_pagination(mock_run):
    mock_run.side_effect = [
        MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "reviewThreads": {
                                    "nodes": [
                                        {
                                            "isResolved": True,
                                            "comments": {"nodes": [{"databaseId": 1}]},
                                        }
                                    ],
                                    "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                                }
                            }
                        }
                    }
                }
            ),
        ),
        MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "reviewThreads": {
                                    "nodes": [
                                        {
                                            "isResolved": True,
                                            "comments": {"nodes": [{"databaseId": 2}]},
                                        }
                                    ],
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                }
                            }
                        }
                    }
                }
            ),
        ),
    ]
    resolved = check_resolved_threads(pr=1, owner="o", repo="r")
    assert resolved == {1, 2}
    assert mock_run.call_count == 2
    # Second call should pass the cursor
    second_call_args = mock_run.call_args_list[1].args[0]
    assert "cursor=c1" in second_call_args


@patch("subprocess.run")
def test_check_resolved_threads_error_returns_empty(mock_run):
    mock_run.side_effect = subprocess.CalledProcessError(1, "gh")
    resolved = check_resolved_threads(pr=1, owner="o", repo="r")
    assert resolved == set()


@patch("subprocess.run")
def test_check_resolved_threads_invalid_json_returns_empty(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="not json")
    resolved = check_resolved_threads(pr=1, owner="o", repo="r")
    assert resolved == set()


@patch("superseded.memory.feedback.check_resolved_threads")
@patch("subprocess.run")
def test_check_pr_feedback_merges_resolved_threads(mock_run, mock_resolved):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=(
            '{"id": 1, "body": "x", "path": "a.py", "line": 1, "reactions": {"+1": 0, "-1": 0}}\n'
            '{"id": 2, "body": "y", "path": "b.py", "line": 2, "reactions": {"+1": 0, "-1": 0}}\n'
        ),
    )
    mock_resolved.return_value = {2}

    feedback = check_pr_feedback(pr=1, repo="o/r")

    assert len(feedback) == 2
    assert feedback[0].get("resolved") is not True
    assert feedback[1]["resolved"] is True


@patch("superseded.memory.feedback.check_resolved_threads")
@patch("subprocess.run")
def test_check_pr_feedback_no_resolved_threads(mock_run, mock_resolved):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout='{"id": 1, "body": "x", "path": "a.py", "line": 1, "reactions": {"+1": 0, "-1": 0}}\n',
    )
    mock_resolved.return_value = set()

    feedback = check_pr_feedback(pr=1, repo="o/r")

    assert len(feedback) == 1
    assert feedback[0].get("resolved") is not True
