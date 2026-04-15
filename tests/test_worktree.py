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


def test_worktree_source_code_root_fallback():
    """When a registered repo has empty path, source_code_root is used."""
    wm = WorktreeManager("/tmp/primary", source_code_root="/opt/repos")
    wm.register_repo("frontend", "")
    assert wm._get_repo_path("frontend") == Path("/opt/repos/frontend")


def test_worktree_source_code_root_not_set_empty_path():
    """When source_code_root is not set and path is empty, returns empty Path."""
    wm = WorktreeManager("/tmp/primary")
    wm.register_repo("frontend", "")
    assert wm._get_repo_path("frontend") == Path("")


def test_worktree_source_code_root_ignored_when_path_set():
    """When a repo has an explicit path, source_code_root is not used."""
    wm = WorktreeManager("/tmp/primary", source_code_root="/opt/repos")
    wm.register_repo("frontend", "/tmp/frontend")
    assert wm._get_repo_path("frontend") == Path("/tmp/frontend")


async def test_worktree_stash_if_dirty():
    """stash_if_dirty stashes uncommitted changes."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _init_git_repo(repo)
        wm = WorktreeManager(str(repo))

        # Modify tracked file to create dirty state
        (repo / "README.md").write_text("modified content")

        stash_ref = await wm.stash_if_dirty()
        assert stash_ref is not None
        assert "superseded" in stash_ref

        # Working tree should be clean now
        result = subprocess.run(
            ["git", "status", "--porcelain"], cwd=str(repo), capture_output=True, text=True
        )
        assert result.stdout.strip() == ""

        await wm.pop_stash(stash_ref)


async def test_worktree_stash_if_clean():
    """stash_if_dirty returns None when working tree is clean."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _init_git_repo(repo)
        wm = WorktreeManager(str(repo))

        stash_ref = await wm.stash_if_dirty()
        assert stash_ref is None


async def test_worktree_pop_stash_none_ref():
    """pop_stash with None ref is a no-op."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _init_git_repo(repo)
        wm = WorktreeManager(str(repo))

        # Should not raise
        await wm.pop_stash(None)


async def test_worktree_create_reuses_existing_branch():
    """create falls back to existing branch if branch already exists."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _init_git_repo(repo)
        wm = WorktreeManager(str(repo))

        # First create sets up the branch
        path1 = await wm.create("SUP-001")
        assert path1.exists()
        await wm.cleanup("SUP-001")

        # Second create should reuse the branch (the fallback path)
        path2 = await wm.create("SUP-001")
        assert path2.exists()
        await wm.cleanup("SUP-001")
