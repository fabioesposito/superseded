from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from superseded.server.checkout import checkout_repo
from superseded.server.repo_manager import RepoManager


def test_repo_manager_disk_usage():
    manager = RepoManager(base_path=Path("/tmp/test"))
    usage = manager.disk_usage()
    assert 0.0 <= usage <= 1.0


def test_repo_manager_cleanup(tmp_path):
    target = tmp_path / "repo"
    target.mkdir()
    (target / "file.txt").write_text("hello")
    manager = RepoManager(base_path=tmp_path)
    manager.cleanup(target)
    assert not target.exists()


def test_repo_manager_cleanup_missing_dir():
    manager = RepoManager(base_path=Path("/tmp/nonexistent"))
    manager.cleanup(Path("/tmp/nonexistent/does/not/exist"))


@patch("superseded.server.checkout.asyncio.create_subprocess_exec")
def test_checkout_repo_calls_git_clone(mock_create):
    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate.return_value = (b"", b"")
    mock_create.return_value = proc

    async def _test():
        return await checkout_repo(
            token="ghp_test_token",
            owner="octocat",
            repo="hello-world",
            ref="abc123",
            base_ref="main",
            tmp_dir="/tmp/test/checkout",
        )

    asyncio.run(_test())

    assert mock_create.called
    call_args = mock_create.call_args[0]
    assert "git" in call_args
    assert "clone" in call_args


@patch("superseded.server.checkout.asyncio.create_subprocess_exec")
def test_checkout_repo_failure_raises(mock_create):
    proc = AsyncMock()
    proc.returncode = 128
    proc.communicate.return_value = (b"", b"repository not found")
    mock_create.return_value = proc

    async def _test():
        return await checkout_repo(
            token="ghp_bad",
            owner="no",
            repo="such-repo",
            ref="abc",
            base_ref="main",
            tmp_dir="/tmp/test/fail",
        )

    with pytest.raises(RuntimeError, match="git clone failed"):
        asyncio.run(_test())
