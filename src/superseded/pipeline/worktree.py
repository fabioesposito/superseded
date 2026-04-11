from __future__ import annotations

import subprocess
from pathlib import Path


class WorktreeManager:
    def __init__(self, repo_path: str) -> None:
        self.repo_path = Path(repo_path)
        self._worktrees_dir = self.repo_path / ".superseded" / "worktrees"

    def _worktree_path(self, issue_id: str) -> Path:
        return self._worktrees_dir / issue_id

    def _branch_name(self, issue_id: str) -> str:
        return f"issue/{issue_id}"

    def _run_git(
        self, *args: str, cwd: str | None = None
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=cwd or str(self.repo_path),
            capture_output=True,
            text=True,
        )

    def create(self, issue_id: str) -> Path:
        worktree_path = self._worktree_path(issue_id)
        branch_name = self._branch_name(issue_id)
        result = self._run_git("worktree", "add", str(worktree_path), "-b", branch_name)
        if result.returncode != 0:
            branch_result = self._run_git(
                "worktree", "add", str(worktree_path), branch_name
            )
            if branch_result.returncode != 0:
                raise RuntimeError(
                    f"Failed to create worktree for {issue_id}: {result.stderr}\n{branch_result.stderr}"
                )
        return worktree_path

    def cleanup(self, issue_id: str) -> None:
        worktree_path = self._worktree_path(issue_id)
        branch_name = self._branch_name(issue_id)
        if worktree_path.exists():
            self._run_git("worktree", "remove", str(worktree_path), "--force")
        self._run_git("branch", "-D", branch_name)

    def get_path(self, issue_id: str) -> Path:
        return self._worktree_path(issue_id)

    def exists(self, issue_id: str) -> bool:
        return self._worktree_path(issue_id).exists()

    def stash_if_dirty(self) -> str | None:
        result = self._run_git("status", "--porcelain")
        if result.stdout.strip():
            stash_result = self._run_git("stash", "push", "-m", "superseded-auto-stash")
            if stash_result.returncode == 0:
                return "superseded-auto-stash"
        return None

    def pop_stash(self, stash_ref: str | None) -> None:
        if stash_ref:
            self._run_git("stash", "pop")
