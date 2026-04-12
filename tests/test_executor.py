from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from superseded.config import SupersededConfig
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

    mock_runner.run_stage_with_retries.return_value = StageResult(
        stage=Stage.SPEC, passed=True, output="spec done"
    )

    issue = Issue(id="SUP-001", title="Test", filepath=ticket_path)
    await db.upsert_issue(issue)

    result = await executor.run_stage(issue, Stage.SPEC, config)
    assert result.passed is True
    assert "spec done" in result.output


async def test_executor_build_stage_creates_worktree(executor_setup):
    executor, db, config, mock_runner, _, repo_path, ticket_path = executor_setup

    mock_runner.run_stage_with_retries.return_value = StageResult(
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

    mock_runner.run_stage_with_retries.return_value = StageResult(
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

    mock_runner.run_stage_with_retries.return_value = StageResult(
        stage=Stage.SPEC, passed=True, output="fixed"
    )

    issue = Issue(id="SUP-001", title="Test", filepath=ticket_path)
    await db.upsert_issue(issue)

    await executor.run_stage(issue, Stage.SPEC, config)

    call_kwargs = mock_runner.run_stage_with_retries.call_args.kwargs
    assert call_kwargs["previous_errors"] == ["prev error"]


async def test_executor_ship_stage_cleans_up_worktree(executor_setup):
    executor, db, config, mock_runner, _, repo_path, ticket_path = executor_setup

    subprocess_run(["git", "init"], cwd=repo_path)
    subprocess_run(["git", "config", "user.email", "test@test.com"], cwd=repo_path)
    subprocess_run(["git", "config", "user.name", "Test"], cwd=repo_path)
    subprocess_run(["git", "add", "."], cwd=repo_path)
    subprocess_run(["git", "commit", "-m", "init"], cwd=repo_path)

    mock_runner.run_stage_with_retries.return_value = StageResult(
        stage=Stage.SHIP, passed=True, output="shipped"
    )

    issue = Issue(id="SUP-001", title="Test", filepath=ticket_path)
    await db.upsert_issue(issue)

    result = await executor.run_stage(issue, Stage.SHIP, config)
    assert result.passed is True


async def test_executor_saves_harness_iteration(executor_setup):
    executor, db, config, mock_runner, _, _, ticket_path = executor_setup

    mock_runner.run_stage_with_retries.return_value = StageResult(
        stage=Stage.SPEC, passed=True, output="ok"
    )

    issue = Issue(id="SUP-001", title="Test", filepath=ticket_path)
    await db.upsert_issue(issue)

    await executor.run_stage(issue, Stage.SPEC, config)

    iterations = await db.get_harness_iterations("SUP-001")
    assert len(iterations) == 1
    assert iterations[0]["attempt"] == 0
    assert iterations[0]["stage"] == "spec"
    assert iterations[0]["exit_code"] == 0


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

    mock_runner.run_stage_with_retries.side_effect = side_effect

    result = await executor.run_stage(issue, Stage.SPEC, config)
    assert result.passed is False
    assert "frontend ok" in result.output
    assert "backend failed" in result.output
    assert call_count == 2


def subprocess_run(args: list[str], cwd: str):
    import subprocess

    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0 and "already exists" not in result.stderr:
        pass  # Allow branch-already-exists errors
    return result
