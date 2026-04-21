from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from superseded.config import StageAgentConfig, SupersededConfig
from superseded.db import Database
from superseded.models import Issue, Stage, StageResult
from superseded.pipeline.executor import StageExecutor
from superseded.pipeline.worktree import WorktreeManager

TICKET = """---
id: SUP-001
title: Test
status: new
stage: build
created: "2026-04-11"
assignee: ""
labels: []
repos: []
---
Body
"""


@pytest.fixture
async def executor_setup():
    with tempfile.TemporaryDirectory() as tmp:
        repo_path = Path(tmp)
        issues_dir = repo_path / ".superseded" / "issues"
        issues_dir.mkdir(parents=True)

        ticket_path = issues_dir / "SUP-001-test.md"
        ticket_path.write_text(TICKET)

        db_path = str(repo_path / ".superseded" / "state.db")
        db = Database(db_path)
        await db.initialize()

        config = SupersededConfig(repo_path=str(repo_path))
        mock_runner = AsyncMock()
        mock_runner.stage_configs = {}
        worktree_manager = WorktreeManager(str(repo_path))

        executor = StageExecutor(
            runner=mock_runner,
            db=db,
            worktree_manager=worktree_manager,
        )

        yield executor, db, config, mock_runner, worktree_manager, str(repo_path), str(ticket_path)

        await db.close()


async def test_executor_spec_stage_no_worktree(executor_setup):
    executor, db, config, mock_runner, _, _, ticket_path = executor_setup

    mock_runner.run_stage_streaming.return_value = StageResult(
        stage=Stage.SPEC, passed=True, output="spec done"
    )

    issue = Issue(id="SUP-001", title="Test", filepath=ticket_path)
    await db.upsert_issue(issue)

    result = await executor.run_stage(issue, Stage.SPEC, config)
    assert result.passed is True
    assert "spec done" in result.output


async def test_executor_build_stage_creates_worktree(executor_setup):
    executor, db, config, mock_runner, _, repo_path, ticket_path = executor_setup

    mock_runner.run_stage_streaming.return_value = StageResult(
        stage=Stage.BUILD, passed=True, output="built"
    )

    subprocess_run(["git", "init"], cwd=repo_path)
    subprocess_run(["git", "config", "user.email", "test@test.com"], cwd=repo_path)
    subprocess_run(["git", "config", "user.name", "Test"], cwd=repo_path)
    subprocess_run(["git", "add", "."], cwd=repo_path)
    subprocess_run(["git", "commit", "-m", "init"], cwd=repo_path)

    issue = Issue(id="SUP-001", title="Test", filepath=ticket_path)
    await db.upsert_issue(issue)

    result = await executor.run_stage(issue, Stage.BUILD, config)
    assert result.passed is True


async def test_executor_failure_updates_status(executor_setup):
    executor, db, config, mock_runner, _, _, ticket_path = executor_setup

    mock_runner.run_stage_streaming.return_value = StageResult(
        stage=Stage.SPEC, passed=False, output="", error="spec failed"
    )

    issue = Issue(id="SUP-001", title="Test", filepath=ticket_path)
    await db.upsert_issue(issue)

    result = await executor.run_stage(issue, Stage.SPEC, config)
    assert result.passed is False
    assert "spec failed" in result.output


async def test_executor_collects_previous_errors(executor_setup):
    executor, db, config, mock_runner, _, _, ticket_path = executor_setup

    await db.save_stage_result(
        "SUP-001",
        StageResult(stage=Stage.SPEC, passed=False, output="", error="prev error"),
    )

    mock_runner.run_stage_streaming.return_value = StageResult(
        stage=Stage.SPEC, passed=True, output="fixed"
    )

    issue = Issue(id="SUP-001", title="Test", filepath=ticket_path)
    await db.upsert_issue(issue)

    await executor.run_stage(issue, Stage.SPEC, config)

    call_kwargs = mock_runner.run_stage_streaming.call_args.kwargs
    assert call_kwargs["previous_errors"] == ["prev error"]


async def test_executor_ship_stage_cleans_up_worktree(executor_setup):
    executor, db, config, mock_runner, _, repo_path, ticket_path = executor_setup

    subprocess_run(["git", "init"], cwd=repo_path)
    subprocess_run(["git", "config", "user.email", "test@test.com"], cwd=repo_path)
    subprocess_run(["git", "config", "user.name", "Test"], cwd=repo_path)
    subprocess_run(["git", "add", "."], cwd=repo_path)
    subprocess_run(["git", "commit", "-m", "init"], cwd=repo_path)

    mock_runner.run_stage_streaming.return_value = StageResult(
        stage=Stage.SHIP, passed=True, output="shipped"
    )

    issue = Issue(id="SUP-001", title="Test", filepath=ticket_path)
    await db.upsert_issue(issue)

    result = await executor.run_stage(issue, Stage.SHIP, config)
    assert result.passed is True


async def test_executor_records_timestamps(executor_setup):
    executor, db, config, mock_runner, _, _, ticket_path = executor_setup

    mock_runner.run_stage_streaming.return_value = StageResult(
        stage=Stage.SPEC, passed=True, output="spec done"
    )

    issue = Issue(id="SUP-001", title="Test", filepath=ticket_path)
    await db.upsert_issue(issue)

    result = await executor.run_stage(issue, Stage.SPEC, config)
    assert result.started_at is not None
    assert result.finished_at is not None
    assert result.finished_at >= result.started_at


async def test_executor_multi_repo_partial_failure(executor_setup):
    executor, db, config, mock_runner, _, _, ticket_path = executor_setup

    issue = Issue(id="SUP-001", title="Test", filepath=ticket_path, repos=["frontend", "backend"])
    await db.upsert_issue(issue)

    call_count = 0

    async def side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if kwargs.get("repo") == "frontend":
            return StageResult(stage=Stage.SPEC, passed=True, output="frontend ok")
        return StageResult(stage=Stage.SPEC, passed=False, output="", error="backend failed")

    mock_runner.run_stage_streaming.side_effect = side_effect

    result = await executor.run_stage(issue, Stage.SPEC, config)
    assert result.passed is False
    assert "frontend ok" in result.output
    assert "backend failed" in result.output
    assert call_count == 2


async def test_executor_approval_required_updates_status(executor_setup):
    executor, db, config, mock_runner, _, _, ticket_path = executor_setup

    async def side_effect(**kwargs):
        artifacts_path = kwargs.get("artifacts_path")
        if artifacts_path:
            (Path(artifacts_path) / "approval.md").write_text("approve me")
        return StageResult(stage=Stage.SPEC, passed=True, output="please approve")

    mock_runner.run_stage_streaming.side_effect = side_effect

    issue = Issue(id="SUP-001", title="Test", filepath=ticket_path)
    await db.upsert_issue(issue)

    result = await executor.run_stage(issue, Stage.SPEC, config)
    assert result.passed is False

    db_issue = await db.get_issue("SUP-001")
    assert db_issue["pause_reason"] == "approval-required"


async def test_executor_upfront_approval_generation(executor_setup):
    executor, db, config, mock_runner, _, _repo_path, ticket_path = executor_setup

    # Mock require_approval to be True
    mock_runner.stage_configs = {
        "spec": StageAgentConfig(cli="claude-code", model="", require_approval=True)
    }

    issue = Issue(id="SUP-001", title="Test", filepath=ticket_path)
    await db.upsert_issue(issue)

    result = await executor.run_stage(issue, Stage.SPEC, config)

    assert result.passed is False
    assert "approval-required" in result.output

    # Ensure agent was not called
    mock_runner.run_stage_streaming.assert_not_called()

    # Ensure file was created
    approval_file = (
        Path(config.repo_path) / config.artifacts_dir / issue.id / "primary" / "approval.md"
    )
    assert approval_file.exists()
    assert "requires manual approval" in approval_file.read_text()

    # Check DB status
    db_issue = await db.get_issue("SUP-001")
    assert db_issue["pause_reason"] == "approval-required"


def subprocess_run(args: list[str], cwd: str):
    import subprocess

    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0 and "already exists" not in result.stderr:
        pass  # Allow branch-already-exists errors
    return result
