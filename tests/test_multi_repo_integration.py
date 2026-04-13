import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

from superseded.agents.factory import AgentFactory
from superseded.config import RepoEntry, SupersededConfig
from superseded.db import Database
from superseded.models import AgentResult, Issue, Stage
from superseded.pipeline.harness import HarnessRunner
from superseded.pipeline.worktree import WorktreeManager
from superseded.tickets.reader import read_issue
from superseded.tickets.writer import write_issue


def _mock_factory(mock_agent):
    factory = AgentFactory()
    factory.create = lambda **kwargs: mock_agent
    return factory


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(path), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(path),
        capture_output=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), capture_output=True)
    (path / "README.md").write_text(f"# {path.name}")
    subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), capture_output=True)


async def test_multi_repo_full_pipeline():
    """A ticket targeting two repos runs BUILD in both with separate worktrees and per-repo results."""
    with tempfile.TemporaryDirectory() as tmp:
        # Set up primary repo
        primary = Path(tmp) / "primary"
        primary.mkdir()
        _init_git_repo(primary)

        # Set up frontend repo
        frontend = Path(tmp) / "frontend"
        frontend.mkdir()
        _init_git_repo(frontend)

        # Set up backend repo
        backend = Path(tmp) / "backend"
        backend.mkdir()
        _init_git_repo(backend)

        # Create config
        config = SupersededConfig(
            repo_path=str(primary),
            repos={
                "frontend": RepoEntry(path=str(frontend)),
                "backend": RepoEntry(path=str(backend)),
            },
        )

        # Create database
        db_path = primary / ".superseded" / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        # Create a multi-repo ticket
        issues_dir = primary / ".superseded" / "issues"
        issues_dir.mkdir(parents=True)
        ticket_content = """---
id: SUP-050
title: Cross-repo feature
status: new
stage: build
created: 2026-01-01
repos:
  - frontend
  - backend
---

Add feature that spans frontend and backend.
"""
        ticket_path = issues_dir / "SUP-050-cross-repo-feature.md"
        write_issue(str(ticket_path), ticket_content)

        issue = read_issue(str(ticket_path))
        assert issue.repos == ["frontend", "backend"]

        await db.upsert_issue(issue)

        # Set up mock agent that always succeeds
        mock_agent = AsyncMock()
        mock_agent.run.return_value = AgentResult(exit_code=0, stdout="build succeeded", stderr="")

        # Create harness runner and configure repos
        runner = HarnessRunner(
            agent_factory=_mock_factory(mock_agent), repo_path=str(primary), max_retries=1
        )
        runner.configure_repos(config.repos)

        # Run BUILD stage (multi-repo)
        artifacts_path = str(primary / ".superseded" / "artifacts" / "SUP-050")
        Path(artifacts_path).mkdir(parents=True, exist_ok=True)

        results = await runner.run_stage_multi_repo(
            issue=issue,
            stage=Stage.BUILD,
            artifacts_path=artifacts_path,
        )

        # Verify both repos were built
        assert "frontend" in results
        assert "backend" in results
        assert results["frontend"].passed is True
        assert results["backend"].passed is True
        assert mock_agent.run.call_count == 2

        # Save per-repo results
        await db.save_stage_result("SUP-050", results["frontend"], repo="frontend")
        await db.save_stage_result("SUP-050", results["backend"], repo="backend")

        # Verify per-repo results in DB
        frontend_results = await db.get_stage_results("SUP-050", repo="frontend")
        assert len(frontend_results) == 1
        assert frontend_results[0]["passed"] is True

        backend_results = await db.get_stage_results("SUP-050", repo="backend")
        assert len(backend_results) == 1
        assert backend_results[0]["passed"] is True

        # Verify worktree manager knows about both repos
        wm = WorktreeManager(str(primary))
        wm.register_repo("frontend", str(frontend))
        wm.register_repo("backend", str(backend))

        fe_path = wm.get_path("SUP-050", repo="frontend")
        be_path = wm.get_path("SUP-050", repo="backend")
        assert "SUP-050__frontend" in str(fe_path)
        assert "SUP-050__backend" in str(be_path)
        assert fe_path != be_path

        await db.close()


async def test_multi_repo_backward_compatible():
    """Single-repo tickets (no repos field) work unchanged."""
    with tempfile.TemporaryDirectory() as tmp:
        primary = Path(tmp) / "primary"
        primary.mkdir()
        _init_git_repo(primary)

        mock_agent = AsyncMock()
        mock_agent.run.return_value = AgentResult(exit_code=0, stdout="spec done", stderr="")

        runner = HarnessRunner(
            agent_factory=_mock_factory(mock_agent), repo_path=str(primary), max_retries=1
        )

        issue = Issue(
            id="SUP-051",
            title="Single repo issue",
            filepath=".superseded/issues/SUP-051-test.md",
        )

        artifacts_path = str(primary / ".superseded" / "artifacts" / "SUP-051")
        Path(artifacts_path).mkdir(parents=True, exist_ok=True)

        results = await runner.run_stage_multi_repo(
            issue=issue,
            stage=Stage.SPEC,
            artifacts_path=artifacts_path,
        )

        assert "primary" in results
        assert len(results) == 1
        assert results["primary"].passed is True
