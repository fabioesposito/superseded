import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

from superseded.agents.claude_code import ClaudeCodeAdapter
from superseded.agents.factory import AgentFactory
from superseded.agents.opencode import OpenCodeAdapter
from superseded.config import StageAgentConfig
from superseded.models import AgentResult, Issue, Stage
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


async def test_harness_retries_on_failure():
    mock_agent = AsyncMock()
    mock_agent.run.side_effect = [
        AgentResult(exit_code=1, stdout="", stderr="build error on line 5"),
        AgentResult(exit_code=1, stdout="", stderr="still failing"),
        AgentResult(exit_code=0, stdout="build succeeded", stderr=""),
    ]

    runner = HarnessRunner(
        agent_factory=_mock_factory(mock_agent), repo_path="/tmp/testrepo", max_retries=3
    )
    with tempfile.TemporaryDirectory() as tmp:
        artifacts_path = Path(tmp) / ".superseded" / "artifacts" / "SUP-001"
        artifacts_path.mkdir(parents=True)
        result = await runner.run_stage_with_retries(
            issue=_make_issue(),
            stage=Stage.BUILD,
            artifacts_path=str(artifacts_path),
        )

    assert result.passed is True
    assert mock_agent.run.call_count == 3


async def test_harness_stops_after_max_retries():
    mock_agent = AsyncMock()
    mock_agent.run.return_value = AgentResult(exit_code=1, stdout="", stderr="persistent failure")

    runner = HarnessRunner(
        agent_factory=_mock_factory(mock_agent), repo_path="/tmp/testrepo", max_retries=2
    )
    with tempfile.TemporaryDirectory() as tmp:
        artifacts_path = Path(tmp) / ".superseded" / "artifacts" / "SUP-001"
        artifacts_path.mkdir(parents=True)
        result = await runner.run_stage_with_retries(
            issue=_make_issue(),
            stage=Stage.BUILD,
            artifacts_path=str(artifacts_path),
        )

    assert result.passed is False
    assert "persistent failure" in result.error
    assert mock_agent.run.call_count == 2


async def test_harness_passes_on_first_try():
    mock_agent = AsyncMock()
    mock_agent.run.return_value = AgentResult(exit_code=0, stdout="spec written", stderr="")

    runner = HarnessRunner(
        agent_factory=_mock_factory(mock_agent), repo_path="/tmp/testrepo", max_retries=3
    )
    with tempfile.TemporaryDirectory() as tmp:
        artifacts_path = Path(tmp) / ".superseded" / "artifacts" / "SUP-001"
        artifacts_path.mkdir(parents=True)
        result = await runner.run_stage_with_retries(
            issue=_make_issue(),
            stage=Stage.SPEC,
            artifacts_path=str(artifacts_path),
        )

    assert result.passed is True
    assert mock_agent.run.call_count == 1


async def test_harness_non_retryable_stage_no_retry():
    mock_agent = AsyncMock()
    mock_agent.run.return_value = AgentResult(exit_code=1, stdout="", stderr="ship failed")

    runner = HarnessRunner(
        agent_factory=_mock_factory(mock_agent),
        repo_path="/tmp/testrepo",
        max_retries=3,
        retryable_stages=["build", "verify", "review"],
    )
    with tempfile.TemporaryDirectory() as tmp:
        artifacts_path = Path(tmp) / ".superseded" / "artifacts" / "SUP-001"
        artifacts_path.mkdir(parents=True)
        result = await runner.run_stage_with_retries(
            issue=_make_issue(),
            stage=Stage.SHIP,
            artifacts_path=str(artifacts_path),
        )

    assert result.passed is False
    assert mock_agent.run.call_count == 1


async def test_harness_multi_repo_fan_out():
    """run_stage_multi_repo runs once per target repo."""
    mock_agent = AsyncMock()
    mock_agent.run.return_value = AgentResult(exit_code=0, stdout="build succeeded", stderr="")

    runner = HarnessRunner(
        agent_factory=_mock_factory(mock_agent), repo_path="/tmp/testrepo", max_retries=1
    )

    issue = Issue(
        id="SUP-001",
        title="Multi-repo issue",
        filepath=".superseded/issues/SUP-001-test.md",
        repos=["frontend", "backend"],
    )

    with tempfile.TemporaryDirectory() as tmp:
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
    # Agent should be called twice (once per repo)
    assert mock_agent.run.call_count == 2


async def test_harness_multi_repo_single_repo_fallback():
    """run_stage_multi_repo falls back to single-repo when issue.repos is empty."""
    mock_agent = AsyncMock()
    mock_agent.run.return_value = AgentResult(exit_code=0, stdout="build succeeded", stderr="")

    runner = HarnessRunner(
        agent_factory=_mock_factory(mock_agent), repo_path="/tmp/testrepo", max_retries=1
    )

    issue = Issue(
        id="SUP-001",
        title="Single repo issue",
        filepath=".superseded/issues/SUP-001-test.md",
    )

    with tempfile.TemporaryDirectory() as tmp:
        artifacts_path = Path(tmp) / ".superseded" / "artifacts" / "SUP-001"
        artifacts_path.mkdir(parents=True)

        results = await runner.run_stage_multi_repo(
            issue=issue,
            stage=Stage.BUILD,
            artifacts_path=str(artifacts_path),
        )

    assert "primary" in results
    assert len(results) == 1
    assert mock_agent.run.call_count == 1


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
