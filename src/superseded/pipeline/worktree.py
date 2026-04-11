from __future__ import annotations

import asyncio
from pathlib import Path


class WorktreeManager:
    def __init__(self, repo_path: str) -> None:
        self.repo_path = Path(repo_path)
        self._worktrees_dir = self.repo_path / ".superseded" / "worktrees"
        self._repo_registry: dict[str, Path] = {}

    def register_repo(self, name: str, repo_path: str) -> None:
        self._repo_registry[name] = Path(repo_path)

    def _get_repo_path(self, repo: str | None = None) -> Path:
        if repo and repo != "primary":
            if repo not in self._repo_registry:
                raise ValueError(
                    f"Unknown repo: {repo}. Registered: {list(self._repo_registry.keys())}"
                )
            return self._repo_registry[repo]
        return self.repo_path

    def _worktree_path(self, issue_id: str, repo: str | None = None) -> Path:
        if repo and repo != "primary":
            return self._worktrees_dir / f"{issue_id}__{repo}"
        return self._worktrees_dir / issue_id

    def _branch_name(self, issue_id: str, repo: str | None = None) -> str:
        if repo and repo != "primary":
            return f"issue/{issue_id}/{repo}"
        return f"issue/{issue_id}"

    async def _run_git(
        self, *args: str, cwd: str | None = None
    ) -> asyncio.subprocess.Process:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=cwd or str(self.repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return type(
            "_Result",
            (),
            {
                "returncode": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
            },
        )()

    async def create(self, issue_id: str, repo: str | None = None) -> Path:
        repo_path = self._get_repo_path(repo)
        worktree_path = self._worktree_path(issue_id, repo)
        branch_name = self._branch_name(issue_id, repo)
        result = await self._run_git(
            "worktree",
            "add",
            str(worktree_path),
            "-b",
            branch_name,
            cwd=str(repo_path),
        )
        if result.returncode != 0:
            branch_result = await self._run_git(
                "worktree",
                "add",
                str(worktree_path),
                branch_name,
                cwd=str(repo_path),
            )
            if branch_result.returncode != 0:
                raise RuntimeError(
                    f"Failed to create worktree for {issue_id}: {result.stderr}\n{branch_result.stderr}"
                )
        return worktree_path

    async def cleanup(self, issue_id: str, repo: str | None = None) -> None:
        repo_path = self._get_repo_path(repo)
        worktree_path = self._worktree_path(issue_id, repo)
        branch_name = self._branch_name(issue_id, repo)
        if worktree_path.exists():
            await self._run_git(
                "worktree", "remove", str(worktree_path), "--force", cwd=str(repo_path)
            )
        await self._run_git("branch", "-D", branch_name, cwd=str(repo_path))

    def get_path(self, issue_id: str, repo: str | None = None) -> Path:
        return self._worktree_path(issue_id, repo)

    def exists(self, issue_id: str, repo: str | None = None) -> bool:
        return self._worktree_path(issue_id, repo).exists()

    async def stash_if_dirty(self) -> str | None:
        result = await self._run_git("status", "--porcelain")
        if result.stdout.strip():
            stash_result = await self._run_git(
                "stash", "push", "-m", "superseded-auto-stash"
            )
            if stash_result.returncode == 0:
                return "superseded-auto-stash"
        return None

    async def pop_stash(self, stash_ref: str | None) -> None:
        if stash_ref:
            await self._run_git("stash", "pop")
