import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

from superseded.agents.claude_code import ClaudeCodeAdapter
from superseded.agents.factory import AgentFactory
from superseded.agents.opencode import OpenCodeAdapter
from superseded.config import StageAgentConfig
from superseded.db import Database
from superseded.models import AgentEvent, Issue, Stage
from superseded.pipeline.harness import HarnessRunner


def _make_issue() -> Issue:
    return Issue(
        id="SUP-001",
        title="Test issue",
        filepath=".superseded/issues/SUP-001-test.md",
    )


def _mock_factory(mock_agent):
    factory = AgentFactory()
    factory.create = lambda **kwargs: mock_agent
    return factory


def _make_mock_agent(exit_code: int = 0, stdout: str = "", stderr: str = ""):
    mock_agent = AsyncMock()
    output = stdout or stderr
    # Ensure output meets MIN_OUTPUT_CHARS (50)
    if len(output) < 50:
        output = output + " " + "x" * (50 - len(output))

    async def fake_stream(prompt, context):
        yield AgentEvent(
            event_type="stdout",
            content=output,
            stage=Stage.BUILD,
        )
        yield AgentEvent(
            event_type="status",
            content="",
            stage=Stage.BUILD,
            metadata={"exit_code": exit_code, "duration_ms": 100},
        )

    mock_agent.run_streaming = fake_stream
    return mock_agent


async def test_harness_runs_once():
    mock_agent = _make_mock_agent(exit_code=1, stderr="build error on line 5")

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(str(Path(tmp) / "state.db"))
        await db.initialize()

        runner = HarnessRunner(
            agent_factory=_mock_factory(mock_agent),
            repo_path="/tmp/testrepo",
            db=db,
        )
        artifacts_path = Path(tmp) / ".superseded" / "artifacts" / "SUP-001"
        artifacts_path.mkdir(parents=True)
        result = await runner.run_stage(
            issue=_make_issue(),
            stage=Stage.BUILD,
            artifacts_path=str(artifacts_path),
        )

        assert result.passed is False
        assert "build error on line 5" in result.error

        await db.close()


async def test_harness_passes_on_success():
    mock_agent = _make_mock_agent(exit_code=0, stdout="spec written")

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(str(Path(tmp) / "state.db"))
        await db.initialize()

        runner = HarnessRunner(
            agent_factory=_mock_factory(mock_agent),
            repo_path="/tmp/testrepo",
            db=db,
        )
        artifacts_path = Path(tmp) / ".superseded" / "artifacts" / "SUP-001"
        artifacts_path.mkdir(parents=True)
        result = await runner.run_stage(
            issue=_make_issue(),
            stage=Stage.SPEC,
            artifacts_path=str(artifacts_path),
        )

        assert result.passed is True

        await db.close()


async def test_harness_multi_repo_fan_out():
    """run_stage_multi_repo runs once per target repo."""
    mock_agent = _make_mock_agent(exit_code=0, stdout="build succeeded")

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(str(Path(tmp) / "state.db"))
        await db.initialize()

        runner = HarnessRunner(
            agent_factory=_mock_factory(mock_agent),
            repo_path="/tmp/testrepo",
            db=db,
        )

        issue = Issue(
            id="SUP-001",
            title="Multi-repo issue",
            filepath=".superseded/issues/SUP-001-test.md",
            repos=["frontend", "backend"],
        )

        artifacts_path = Path(tmp) / ".superseded" / "artifacts" / "SUP-001"
        artifacts_path.mkdir(parents=True)

        results = await runner.run_stage_multi_repo(
            issue=issue,
            stage=Stage.BUILD,
            artifacts_path=str(artifacts_path),
        )

        assert "frontend" in results
        assert "backend" in results
        assert results["frontend"].passed is True
        assert results["backend"].passed is True

        await db.close()


async def test_harness_multi_repo_single_repo_fallback():
    """run_stage_multi_repo falls back to single-repo when issue.repos is empty."""
    mock_agent = _make_mock_agent(exit_code=0, stdout="build succeeded")

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(str(Path(tmp) / "state.db"))
        await db.initialize()

        runner = HarnessRunner(
            agent_factory=_mock_factory(mock_agent),
            repo_path="/tmp/testrepo",
            db=db,
        )

        issue = Issue(
            id="SUP-001",
            title="Single repo issue",
            filepath=".superseded/issues/SUP-001-test.md",
        )

        artifacts_path = Path(tmp) / ".superseded" / "artifacts" / "SUP-001"
        artifacts_path.mkdir(parents=True)

        results = await runner.run_stage_multi_repo(
            issue=issue,
            stage=Stage.BUILD,
            artifacts_path=str(artifacts_path),
        )

        assert "primary" in results
        assert len(results) == 1

        await db.close()


def test_resolve_agent_default():
    factory = AgentFactory(default_agent="claude-code", default_model="")
    runner = HarnessRunner(
        agent_factory=factory,
        repo_path="/tmp/test",
    )
    agent = runner.resolve_agent(Stage.SPEC)
    assert isinstance(agent, ClaudeCodeAdapter)


def test_resolve_agent_stage_override():
    factory = AgentFactory(default_agent="claude-code", default_model="")
    runner = HarnessRunner(
        agent_factory=factory,
        repo_path="/tmp/test",
        stage_configs={
            "build": StageAgentConfig(cli="opencode", model="gpt-4o"),
        },
    )
    agent = runner.resolve_agent(Stage.BUILD)
    assert isinstance(agent, OpenCodeAdapter)
    assert agent.model == "gpt-4o"


def test_resolve_agent_falls_back_to_default():
    factory = AgentFactory(default_agent="claude-code", default_model="sonnet")
    runner = HarnessRunner(
        agent_factory=factory,
        repo_path="/tmp/test",
        stage_configs={
            "build": StageAgentConfig(cli="opencode", model="gpt-4o"),
        },
    )
    agent = runner.resolve_agent(Stage.SPEC)
    assert isinstance(agent, ClaudeCodeAdapter)
    assert agent.model == "sonnet"


async def test_harness_approval_required_updates_status():
    mock_agent = AsyncMock()

    async def side_effect(prompt, context):
        artifacts_path = context.artifacts_path
        if artifacts_path:
            (Path(artifacts_path) / "approval.md").write_text("approve me")
        yield AgentEvent(
            event_type="stdout",
            content="please approve this stage output with sufficient content",
            stage=Stage.BUILD,
        )
        yield AgentEvent(
            event_type="status",
            content="",
            stage=Stage.BUILD,
            metadata={"exit_code": 0, "duration_ms": 100},
        )

    mock_agent.run_streaming = side_effect

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(str(Path(tmp) / "state.db"))
        await db.initialize()

        runner = HarnessRunner(
            agent_factory=_mock_factory(mock_agent),
            repo_path="/tmp/testrepo",
            db=db,
        )
        artifacts_path = Path(tmp) / ".superseded" / "artifacts" / "SUP-001"
        artifacts_path.mkdir(parents=True)
        result = await runner.run_stage(
            issue=_make_issue(),
            stage=Stage.BUILD,
            artifacts_path=str(artifacts_path),
        )

        assert result.passed is False
        assert result.error == "approval-required"

        await db.close()


async def test_harness_auto_retries_on_failure():
    """Harness automatically retries once on failure when auto_retry is enabled."""
    call_count = 0
    mock_agent = AsyncMock()

    async def flaky_stream(prompt, context):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield AgentEvent(
                event_type="stderr", content="lint error on line 5", stage=Stage.BUILD,
            )
            yield AgentEvent(
                event_type="status", content="", stage=Stage.BUILD,
                metadata={"exit_code": 1, "duration_ms": 100},
            )
        else:
            yield AgentEvent(
                event_type="stdout", content="Build succeeded with enough output content here for test", stage=Stage.BUILD,
            )
            yield AgentEvent(
                event_type="status", content="", stage=Stage.BUILD,
                metadata={"exit_code": 0, "duration_ms": 100},
            )

    mock_agent.run_streaming = flaky_stream

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(str(Path(tmp) / "state.db"))
        await db.initialize()

        runner = HarnessRunner(
            agent_factory=_mock_factory(mock_agent),
            repo_path="/tmp/testrepo",
            db=db,
            auto_retry=True,
            max_auto_retries=1,
        )
        artifacts_path = Path(tmp) / ".superseded" / "artifacts" / "SUP-001"
        artifacts_path.mkdir(parents=True)
        result = await runner.run_stage(
            issue=_make_issue(),
            stage=Stage.BUILD,
            artifacts_path=str(artifacts_path),
        )
        assert result.passed is True
        assert call_count == 2
        await db.close()


async def test_harness_enforces_resource_limits():
    """Harness checks resource limits during execution."""
    from superseded.harness.lifecycle import ResourceLimits

    mock_agent = _make_mock_agent(exit_code=0, stdout="build succeeded with enough output content here for the minimum")

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(str(Path(tmp) / "state.db"))
        await db.initialize()

        runner = HarnessRunner(
            agent_factory=_mock_factory(mock_agent),
            repo_path="/tmp/testrepo",
            db=db,
        )
        artifacts_path = Path(tmp) / ".superseded" / "artifacts" / "SUP-001"
        artifacts_path.mkdir(parents=True)

        # With generous limits, should pass
        result = await runner.run_stage(
            issue=_make_issue(),
            stage=Stage.BUILD,
            artifacts_path=str(artifacts_path),
            resource_limits=ResourceLimits(max_wall_time_seconds=600),
        )
        assert result.passed is True

        await db.close()
