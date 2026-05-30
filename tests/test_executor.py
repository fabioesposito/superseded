from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from superseded.agents.factory import AgentFactory
from superseded.config import StageAgentConfig, SupersededConfig
from superseded.db import Database
from superseded.models import AgentEvent, Issue, Stage, StageResult
from superseded.pipeline.events import PipelineEventManager
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


def _make_mock_agent(stage_result: StageResult):
    mock_agent = AsyncMock()
    output = stage_result.output or stage_result.error or "output"
    # Ensure output meets MIN_OUTPUT_CHARS (50)
    if len(output) < 50:
        output = output + " " + "x" * (50 - len(output))

    async def fake_stream(prompt, context):
        yield AgentEvent(
            event_type="stdout",
            content=output,
            stage=stage_result.stage,
        )
        yield AgentEvent(
            event_type="status",
            content="",
            stage=stage_result.stage,
            metadata={"exit_code": 0 if stage_result.passed else 1, "duration_ms": 100},
        )

    mock_agent.run_streaming = fake_stream
    return mock_agent


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
        mock_runner.repo_path = str(repo_path)
        mock_runner.agent_factory = AgentFactory()
        mock_runner.stage_configs = {}
        mock_runner.event_manager = PipelineEventManager()
        worktree_manager = WorktreeManager(str(repo_path))

        executor = StageExecutor(
            runner=mock_runner,
            db=db,
            worktree_manager=worktree_manager,
        )

        yield executor, db, config, mock_runner, worktree_manager, str(repo_path), str(ticket_path)

        await db.close()


async def test_executor_spec_stage_no_worktree(executor_setup):
    executor, db, config, _mock_runner, _, _, ticket_path = executor_setup

    result = StageResult(stage=Stage.SPEC, passed=True, output="spec done")
    mock_agent = _make_mock_agent(result)
    executor._harness.resolve_agent = lambda stage: mock_agent

    issue = Issue(id="SUP-001", title="Test", filepath=ticket_path)
    await db.upsert_issue(issue)

    result = await executor.run_stage(issue, Stage.SPEC, config)
    assert result.passed is True
    assert "spec done" in result.output


async def test_executor_build_stage_creates_worktree(executor_setup):
    executor, db, config, _mock_runner, _, repo_path, ticket_path = executor_setup

    result = StageResult(stage=Stage.BUILD, passed=True, output="built def main(): return True")
    mock_agent = _make_mock_agent(result)
    executor._harness.resolve_agent = lambda stage: mock_agent

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
    executor, db, config, _mock_runner, _, _, ticket_path = executor_setup

    result = StageResult(stage=Stage.SPEC, passed=False, output="", error="spec failed")
    mock_agent = _make_mock_agent(result)
    executor._harness.resolve_agent = lambda stage: mock_agent

    issue = Issue(id="SUP-001", title="Test", filepath=ticket_path)
    await db.upsert_issue(issue)

    result = await executor.run_stage(issue, Stage.SPEC, config)
    assert result.passed is False
    assert "spec failed" in result.output


async def test_executor_collects_previous_errors(executor_setup):
    executor, db, config, _mock_runner, _, _, ticket_path = executor_setup

    await db.save_stage_result(
        "SUP-001",
        StageResult(stage=Stage.SPEC, passed=False, output="", error="prev error"),
    )

    result = StageResult(stage=Stage.SPEC, passed=True, output="fixed")
    mock_agent = _make_mock_agent(result)
    prompts = []
    original_run = mock_agent.run_streaming

    async def capture_stream(prompt, context):
        prompts.append(prompt)
        async for event in original_run(prompt, context):
            yield event

    mock_agent.run_streaming = capture_stream
    executor._harness.resolve_agent = lambda stage: mock_agent

    issue = Issue(id="SUP-001", title="Test", filepath=ticket_path)
    await db.upsert_issue(issue)

    await executor.run_stage(issue, Stage.SPEC, config)

    assert len(prompts) == 1
    assert "prev error" in prompts[0]


async def test_executor_ship_stage_cleans_up_worktree(executor_setup):
    executor, db, config, _mock_runner, _, repo_path, ticket_path = executor_setup

    subprocess_run(["git", "init"], cwd=repo_path)
    subprocess_run(["git", "config", "user.email", "test@test.com"], cwd=repo_path)
    subprocess_run(["git", "config", "user.name", "Test"], cwd=repo_path)
    subprocess_run(["git", "add", "."], cwd=repo_path)
    subprocess_run(["git", "commit", "-m", "init"], cwd=repo_path)

    result = StageResult(stage=Stage.SHIP, passed=True, output="shipped")
    mock_agent = _make_mock_agent(result)
    executor._harness.resolve_agent = lambda stage: mock_agent
    executor._harness._check_gh_auth = AsyncMock(return_value=(True, ""))

    issue = Issue(id="SUP-001", title="Test", filepath=ticket_path)
    await db.upsert_issue(issue)

    result = await executor.run_stage(issue, Stage.SHIP, config)
    assert result.passed is True


async def test_executor_records_timestamps(executor_setup):
    executor, db, config, _mock_runner, _, _, ticket_path = executor_setup

    result = StageResult(stage=Stage.SPEC, passed=True, output="spec done")
    mock_agent = _make_mock_agent(result)
    executor._harness.resolve_agent = lambda stage: mock_agent

    issue = Issue(id="SUP-001", title="Test", filepath=ticket_path)
    await db.upsert_issue(issue)

    result = await executor.run_stage(issue, Stage.SPEC, config)
    assert result.started_at is not None
    assert result.finished_at is not None
    assert result.finished_at >= result.started_at


async def test_executor_multi_repo_partial_failure(executor_setup):
    executor, db, config, _mock_runner, _, _, ticket_path = executor_setup

    issue = Issue(id="SUP-001", title="Test", filepath=ticket_path, repos=["frontend", "backend"])
    await db.upsert_issue(issue)

    call_count = 0

    def mock_resolve(stage):
        nonlocal call_count
        call_count += 1
        current_call = call_count
        mock_agent = AsyncMock()

        async def fake_stream(prompt, context):
            if current_call == 1:
                yield AgentEvent(
                    event_type="stdout",
                    content="frontend ok",
                    stage=Stage.SPEC,
                )
                yield AgentEvent(
                    event_type="status",
                    content="",
                    stage=Stage.SPEC,
                    metadata={"exit_code": 0, "duration_ms": 100},
                )
            else:
                yield AgentEvent(
                    event_type="stdout",
                    content="backend failed",
                    stage=Stage.SPEC,
                )
                yield AgentEvent(
                    event_type="status",
                    content="",
                    stage=Stage.SPEC,
                    metadata={"exit_code": 1, "duration_ms": 100},
                )

        mock_agent.run_streaming = fake_stream
        return mock_agent

    executor._harness.resolve_agent = mock_resolve

    result = await executor.run_stage(issue, Stage.SPEC, config)
    assert result.passed is False
    assert "frontend ok" in result.output
    assert "backend failed" in result.output
    assert call_count == 2


async def test_executor_approval_required_updates_status(executor_setup):
    executor, db, config, _mock_runner, _, _, ticket_path = executor_setup

    mock_agent = AsyncMock()

    async def fake_stream(prompt, context):
        artifacts_path = context.artifacts_path
        if artifacts_path:
            (Path(artifacts_path) / "approval.md").write_text("approve me")
        yield AgentEvent(
            event_type="stdout",
            content="please approve this stage with sufficient content for minimum",
            stage=Stage.SPEC,
        )
        yield AgentEvent(
            event_type="status",
            content="",
            stage=Stage.SPEC,
            metadata={"exit_code": 0, "duration_ms": 100},
        )

    mock_agent.run_streaming = fake_stream
    executor._harness.resolve_agent = lambda stage: mock_agent

    issue = Issue(id="SUP-001", title="Test", filepath=ticket_path)
    await db.upsert_issue(issue)

    result = await executor.run_stage(issue, Stage.SPEC, config)
    assert result.passed is False

    db_issue = await db.get_issue("SUP-001")
    assert db_issue["pause_reason"] == "approval-required"


async def test_executor_upfront_approval_generation(executor_setup):
    executor, db, config, _mock_runner, _, _repo_path, ticket_path = executor_setup

    executor._harness.stage_configs = {
        "spec": StageAgentConfig(cli="claude-code", model="", require_approval=True)
    }

    mock_agent = AsyncMock()
    mock_agent.run_streaming = AsyncMock()
    executor._harness.resolve_agent = lambda stage: mock_agent

    issue = Issue(id="SUP-001", title="Test", filepath=ticket_path)
    await db.upsert_issue(issue)

    result = await executor.run_stage(issue, Stage.SPEC, config)

    assert result.passed is False
    assert "approval-required" in result.output

    mock_agent.run_streaming.assert_not_called()

    approval_file = (
        Path(config.repo_path) / config.artifacts_dir / issue.id / "primary" / "approval.md"
    )
    assert approval_file.exists()
    assert "requires manual approval" in approval_file.read_text()

    db_issue = await db.get_issue("SUP-001")
    assert db_issue["pause_reason"] == "approval-required"


def subprocess_run(args: list[str], cwd: str):
    import subprocess

    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0 and "already exists" not in result.stderr:
        pass
    return result
