from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GitResult:
    returncode: int
    stdout: str
    stderr: str


class WorktreeManager:
    def __init__(self, repo_path: str) -> None:
        self.repo_path = Path(repo_path)
        self._worktrees_dir = self.repo_path / ".superseded" / "worktrees"
        self._repo_registry: dict[str, Path] = {}
        self._git_urls: dict[str, str] = {}

    def register_repo(self, name: str, repo_path: str, git_url: str = "") -> None:
        self._repo_registry[name] = Path(repo_path)
        if git_url:
            self._git_urls[name] = git_url

    async def _ensure_repo_exists(self, repo: str, github_token: str = "") -> None:
        repo_path = self._get_repo_path(repo)
        if repo_path.exists():
            return
        git_url = self._git_urls.get(repo, "")
        if not git_url:
            raise ValueError(
                f"Repo path {repo_path} does not exist and no git_url configured for '{repo}'"
            )
        repo_path.parent.mkdir(parents=True, exist_ok=True)

        clone_url = git_url
        if github_token and "github.com" in git_url:
            clone_url = git_url.replace(
                "https://github.com/", f"https://{github_token}@github.com/"
            )

        result = await self._run_git("clone", clone_url, str(repo_path))
        if result.returncode != 0:
            raise RuntimeError(f"Failed to clone {git_url} to {repo_path}: {result.stderr}")

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

    async def _run_git(self, *args: str, cwd: str | None = None) -> GitResult:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=cwd or str(self.repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return GitResult(
            returncode=proc.returncode,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )

    async def create(self, issue_id: str, repo: str | None = None) -> Path:
        if repo and repo != "primary":
            await self._ensure_repo_exists(repo)
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

    async def stash_if_dirty(self, repo: str | None = None) -> str | None:
        repo_path = self._get_repo_path(repo)
        result = await self._run_git("status", "--porcelain", cwd=str(repo_path))
        if result.stdout.strip():
            stash_result = await self._run_git(
                "stash", "push", "-m", "superseded-auto-stash", cwd=str(repo_path)
            )
            if stash_result.returncode == 0:
                return "superseded-auto-stash"
        return None

    async def pop_stash(self, stash_ref: str | None, repo: str | None = None) -> None:
        if stash_ref:
            repo_path = self._get_repo_path(repo)
            await self._run_git("stash", "pop", cwd=str(repo_path))
