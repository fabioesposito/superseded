import subprocess
import tempfile
from pathlib import Path

import pytest

from superseded.pipeline.worktree import WorktreeManager


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(path), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(path),
        capture_output=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), capture_output=True)
    (path / "README.md").write_text("test")
    subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), capture_output=True)


async def test_worktree_create():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _init_git_repo(repo)
        wm = WorktreeManager(str(repo))
        worktree_path = await wm.create("SUP-001")
        assert worktree_path.exists()
        assert (worktree_path / "README.md").read_text() == "test"
        await wm.cleanup("SUP-001")


async def test_worktree_cleanup():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _init_git_repo(repo)
        wm = WorktreeManager(str(repo))
        worktree_path = await wm.create("SUP-002")
        assert worktree_path.exists()
        await wm.cleanup("SUP-002")
        assert not worktree_path.exists()


def test_worktree_get_path():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _init_git_repo(repo)
        wm = WorktreeManager(str(repo))
        path = wm.get_path("SUP-001")
        assert "SUP-001" in str(path)


async def test_worktree_exists():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _init_git_repo(repo)
        wm = WorktreeManager(str(repo))
        assert wm.exists("SUP-001") is False
        await wm.create("SUP-001")
        assert wm.exists("SUP-001") is True
        await wm.cleanup("SUP-001")


async def test_worktree_manager_multi_repo():
    """WorktreeManager can create worktrees keyed by (issue_id, repo_name)."""
    with tempfile.TemporaryDirectory() as tmp:
        primary = Path(tmp) / "primary"
        primary.mkdir()
        _init_git_repo(primary)

        frontend = Path(tmp) / "frontend"
        frontend.mkdir()
        _init_git_repo(frontend)

        wm = WorktreeManager(str(primary))
        wm.register_repo("frontend", str(frontend))

        # Create frontend worktree
        fe_path = await wm.create("SUP-001", repo="frontend")
        assert fe_path.exists()
        assert "SUP-001__frontend" in str(fe_path)
        assert (fe_path / "README.md").read_text() == "test"

        # Create primary worktree (separate)
        primary_path = await wm.create("SUP-001")
        assert primary_path.exists()
        assert primary_path != fe_path

        # Verify get_path
        assert wm.get_path("SUP-001", repo="frontend") == fe_path
        assert wm.get_path("SUP-001") == primary_path

        # Verify exists
        assert wm.exists("SUP-001", repo="frontend") is True
        assert wm.exists("SUP-001") is True

        await wm.cleanup("SUP-001", repo="frontend")
        await wm.cleanup("SUP-001")


def test_worktree_register_repo():
    """register_repo adds a named repo to the manager."""
    wm = WorktreeManager("/tmp/primary")
    wm.register_repo("frontend", "/tmp/frontend")
    wm.register_repo("backend", "/tmp/backend")
    assert wm._repo_registry["frontend"] == Path("/tmp/frontend")
    assert wm._repo_registry["backend"] == Path("/tmp/backend")


def test_worktree_unknown_repo_raises():
    """Accessing an unregistered repo raises ValueError."""
    wm = WorktreeManager("/tmp/primary")
    with pytest.raises(ValueError, match="Unknown repo"):
        wm._get_repo_path("nonexistent")
