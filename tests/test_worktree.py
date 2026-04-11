import subprocess
import tempfile
from pathlib import Path

from superseded.pipeline.worktree import WorktreeManager


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(path), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(path),
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=str(path), capture_output=True
    )
    (path / "README.md").write_text("test")
    subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), capture_output=True)


def test_worktree_create():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _init_git_repo(repo)
        wm = WorktreeManager(str(repo))
        worktree_path = wm.create("SUP-001")
        assert worktree_path.exists()
        assert (worktree_path / "README.md").read_text() == "test"
        wm.cleanup("SUP-001")


def test_worktree_cleanup():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _init_git_repo(repo)
        wm = WorktreeManager(str(repo))
        worktree_path = wm.create("SUP-002")
        assert worktree_path.exists()
        wm.cleanup("SUP-002")
        assert not worktree_path.exists()


def test_worktree_get_path():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _init_git_repo(repo)
        wm = WorktreeManager(str(repo))
        path = wm.get_path("SUP-001")
        assert "SUP-001" in str(path)


def test_worktree_exists():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _init_git_repo(repo)
        wm = WorktreeManager(str(repo))
        assert wm.exists("SUP-001") is False
        wm.create("SUP-001")
        assert wm.exists("SUP-001") is True
        wm.cleanup("SUP-001")
