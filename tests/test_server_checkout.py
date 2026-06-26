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


def test_repo_manager_job_dir_normal(tmp_path):
    manager = RepoManager(base_path=tmp_path)
    p = manager.job_dir(123, "octocat", "hello-world", 42)
    assert p == tmp_path / "123" / "octocat" / "hello-world" / "42"


def test_repo_manager_job_dir_rejects_traversal_owner(tmp_path):
    manager = RepoManager(base_path=tmp_path)
    with pytest.raises(ValueError, match="owner"):
        manager.job_dir(123, "../..", "repo", 42)


def test_repo_manager_job_dir_rejects_traversal_repo(tmp_path):
    manager = RepoManager(base_path=tmp_path)
    with pytest.raises(ValueError, match="repo"):
        manager.job_dir(123, "octocat", "..", 42)


def test_repo_manager_job_dir_rejects_slash_in_owner(tmp_path):
    manager = RepoManager(base_path=tmp_path)
    with pytest.raises(ValueError, match="owner"):
        manager.job_dir(123, "evil/owner", "repo", 42)


def test_repo_manager_job_dir_rejects_negative_pr(tmp_path):
    manager = RepoManager(base_path=tmp_path)
    with pytest.raises(ValueError, match="pr"):
        manager.job_dir(123, "octocat", "repo", -1)


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
            tmp_dir="/tmp/test/checkout",
        )

    asyncio.run(_test())

    assert mock_create.call_count == 2
    first_call_args = mock_create.call_args_list[0][0]
    assert "clone" in first_call_args
    second_call_args = mock_create.call_args_list[1][0]
    assert "checkout" in second_call_args
    assert "abc123" in second_call_args


@patch("superseded.server.checkout.asyncio.create_subprocess_exec")
def test_checkout_repo_token_not_in_clone_args(mock_create):
    """Token must NOT appear in the clone command args (process-table visible)."""
    import base64

    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate.return_value = (b"", b"")
    mock_create.return_value = proc

    async def _test():
        return await checkout_repo(
            token="ghp_SECRET_TOKEN",
            owner="octocat",
            repo="hello-world",
            ref="abc123",
            tmp_dir="/tmp/test/checkout",
        )

    asyncio.run(_test())

    clone_call = mock_create.call_args_list[0]
    all_args = list(clone_call[0])
    joined = " ".join(str(a) for a in all_args)
    assert "ghp_SECRET_TOKEN" not in joined
    assert "x-access-token:ghp_SECRET_TOKEN" not in joined
    env = clone_call[1].get("env", {})
    expected_b64 = base64.b64encode(b"x-access-token:ghp_SECRET_TOKEN").decode()
    assert any(expected_b64 in str(v) for v in env.values()), (
        "Token should be passed base64-encoded via env var, not in args"
    )


@patch("superseded.server.checkout.asyncio.create_subprocess_exec")
def test_checkout_repo_error_redacts_token(mock_create):
    """RuntimeError on clone failure must not include the token."""
    proc = AsyncMock()
    proc.returncode = 128
    proc.communicate.return_value = (
        b"",
        b"fatal: could not read https://x-access-token:ghp_SECRET@github.com/x/y.git",
    )
    mock_create.return_value = proc

    async def _test():
        return await checkout_repo(
            token="ghp_SECRET",
            owner="no",
            repo="such-repo",
            ref="abc",
            tmp_dir="/tmp/test/fail",
        )

    with pytest.raises(RuntimeError, match="git clone failed") as exc_info:
        asyncio.run(_test())
    assert "ghp_SECRET" not in str(exc_info.value)


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
            tmp_dir="/tmp/test/fail",
        )

    with pytest.raises(RuntimeError, match="git clone failed"):
        asyncio.run(_test())


@patch("superseded.server.checkout.asyncio.create_subprocess_exec")
def test_checkout_repo_does_not_use_branch_flag_for_sha(mock_create):
    """When ref is a SHA, --branch should not be used; git checkout should be called instead."""
    call_log = []

    async def fake_exec(*args, **kwargs):
        call_log.append(args)
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate.return_value = (b"", b"")
        return proc

    mock_create.side_effect = fake_exec

    async def _test():
        return await checkout_repo(
            token="ghp_test_token",
            owner="octocat",
            repo="hello-world",
            ref="abc123def456",
            tmp_dir="/tmp/test/checkout",
        )

    asyncio.run(_test())

    assert len(call_log) == 2, (
        f"Expected 2 subprocess calls (clone + checkout), got {len(call_log)}"
    )
    clone_cmd = call_log[0]
    assert "--branch" not in clone_cmd, f"--branch should not be used for SHA refs: {clone_cmd}"
    checkout_cmd = call_log[1]
    assert "checkout" in checkout_cmd, f"Expected git checkout command: {checkout_cmd}"
    assert "abc123def456" in checkout_cmd, f"SHA should be in checkout command: {checkout_cmd}"
