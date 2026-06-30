from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

from superseded.incremental import IncrementalDiffError, fetch_incremental_diff


@pytest.fixture
def mock_run(monkeypatch):
    m = MagicMock()
    monkeypatch.setattr("superseded.incremental.subprocess.run", m)
    return m


def _ok(stdout: str):
    return MagicMock(stdout=stdout, returncode=0)


def test_ahead_returns_patch(mock_run):
    mock_run.side_effect = [
        _ok('{"status": "ahead", "total_commits": 3}'),
        _ok("diff --git a/x.py b/x.py\n+new\n"),
    ]
    diff, status = fetch_incremental_diff("owner", "repo", "base", "head")
    assert status == "ahead"
    assert diff == "diff --git a/x.py b/x.py\n+new\n"


def test_identical_returns_none_diff(mock_run):
    mock_run.return_value = _ok('{"status": "identical", "total_commits": 0}')
    diff, status = fetch_incremental_diff("owner", "repo", "base", "head")
    assert status == "identical"
    assert diff is None


def test_diverged_returns_none_diff(mock_run):
    mock_run.return_value = _ok('{"status": "diverged", "total_commits": 5}')
    diff, status = fetch_incremental_diff("owner", "repo", "base", "head")
    assert status == "diverged"
    assert diff is None


def test_behind_is_normalized_to_diverged(mock_run):
    mock_run.return_value = _ok('{"status": "behind", "total_commits": 0}')
    diff, status = fetch_incremental_diff("owner", "repo", "base", "head")
    assert status == "diverged"
    assert diff is None


def test_called_process_error_raises_incremental_error(mock_run):
    mock_run.side_effect = subprocess.CalledProcessError(1, "gh", stderr="boom")
    with pytest.raises(IncrementalDiffError):
        fetch_incremental_diff("owner", "repo", "base", "head")


def test_file_not_found_raises_incremental_error(mock_run):
    mock_run.side_effect = FileNotFoundError("gh")
    with pytest.raises(IncrementalDiffError):
        fetch_incremental_diff("owner", "repo", "base", "head")


def test_status_call_uses_compare_endpoint(mock_run):
    mock_run.side_effect = [
        _ok('{"status": "ahead", "total_commits": 1}'),
        _ok("patch"),
    ]
    fetch_incremental_diff("owner", "repo", "aaa", "bbb")
    status_cmd = mock_run.call_args_list[0].args[0]
    assert status_cmd[:3] == ["gh", "api", "repos/owner/repo/compare/aaa...bbb"]


def test_diff_call_uses_diff_accept_header(mock_run):
    mock_run.side_effect = [
        _ok('{"status": "ahead", "total_commits": 1}'),
        _ok("patch"),
    ]
    fetch_incremental_diff("owner", "repo", "aaa", "bbb")
    diff_call = mock_run.call_args_list[1]
    diff_cmd = diff_call.args[0]
    assert "repos/owner/repo/compare/aaa...bbb" in diff_cmd
    assert "-H" in diff_cmd
    accept_idx = diff_cmd.index("-H") + 1
    assert diff_cmd[accept_idx] == "Accept: application/vnd.github.v3.diff"
