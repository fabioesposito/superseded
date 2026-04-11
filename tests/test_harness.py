import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

from superseded.models import AgentResult, Issue, Stage
from superseded.pipeline.harness import HarnessRunner


def _make_issue() -> Issue:
    return Issue(
        id="SUP-001",
        title="Test issue",
        filepath=".superseded/issues/SUP-001-test.md",
    )


async def test_harness_retries_on_failure():
    mock_agent = AsyncMock()
    mock_agent.run.side_effect = [
        AgentResult(exit_code=1, stdout="", stderr="build error on line 5"),
        AgentResult(exit_code=1, stdout="", stderr="still failing"),
        AgentResult(exit_code=0, stdout="build succeeded", stderr=""),
    ]

    runner = HarnessRunner(agent=mock_agent, repo_path="/tmp/testrepo", max_retries=3)
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
    mock_agent.run.return_value = AgentResult(
        exit_code=1, stdout="", stderr="persistent failure"
    )

    runner = HarnessRunner(agent=mock_agent, repo_path="/tmp/testrepo", max_retries=2)
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
    mock_agent.run.return_value = AgentResult(
        exit_code=0, stdout="spec written", stderr=""
    )

    runner = HarnessRunner(agent=mock_agent, repo_path="/tmp/testrepo", max_retries=3)
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
    mock_agent.run.return_value = AgentResult(
        exit_code=1, stdout="", stderr="ship failed"
    )

    runner = HarnessRunner(
        agent=mock_agent,
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
    mock_agent.run.return_value = AgentResult(
        exit_code=0, stdout="build succeeded", stderr=""
    )

    runner = HarnessRunner(agent=mock_agent, repo_path="/tmp/testrepo", max_retries=1)

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
    mock_agent.run.return_value = AgentResult(
        exit_code=0, stdout="build succeeded", stderr=""
    )

    runner = HarnessRunner(agent=mock_agent, repo_path="/tmp/testrepo", max_retries=1)

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
