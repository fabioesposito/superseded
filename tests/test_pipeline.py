import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

from superseded.agents.factory import AgentFactory
from superseded.models import (
    AgentResult,
    Issue,
    Stage,
)
from superseded.pipeline.harness import HarnessRunner
from superseded.pipeline.prompts import get_prompt_for_stage
from superseded.pipeline.stages import STAGE_DEFINITIONS


def _mock_factory(mock_agent):
    factory = AgentFactory()
    factory.create = lambda **kwargs: mock_agent
    return factory


def test_stage_definitions_exist():
    assert len(STAGE_DEFINITIONS) == 6
    stages = [s.stage for s in STAGE_DEFINITIONS]
    assert stages == [
        Stage.SPEC,
        Stage.PLAN,
        Stage.BUILD,
        Stage.VERIFY,
        Stage.REVIEW,
        Stage.SHIP,
    ]


def test_each_stage_has_prompt():
    for stage_def in STAGE_DEFINITIONS:
        prompt = get_prompt_for_stage(stage_def.stage)
        assert len(prompt) > 50, f"Stage {stage_def.stage} has no prompt"
        assert "skill" in prompt.lower() or "you are" in prompt.lower(), (
            f"Stage {stage_def.stage} prompt doesn't reference a skill or persona"
        )


def test_stage_order():
    from superseded.models import STAGE_ORDER

    assert STAGE_ORDER == [
        Stage.SPEC,
        Stage.PLAN,
        Stage.BUILD,
        Stage.VERIFY,
        Stage.REVIEW,
        Stage.SHIP,
    ]


async def test_harness_processes_stage():
    mock_agent = AsyncMock()
    mock_agent.run.return_value = AgentResult(exit_code=0, stdout="spec written", stderr="")

    runner = HarnessRunner(agent_factory=_mock_factory(mock_agent), repo_path="/tmp/testrepo")

    issue = Issue(
        id="SUP-001",
        title="Add rate limiting",
        filepath=".superseded/issues/SUP-001-add-rate-limiting.md",
    )
    with tempfile.TemporaryDirectory() as tmp:
        artifacts_path = Path(tmp) / ".superseded" / "artifacts" / "SUP-001"
        artifacts_path.mkdir(parents=True)
        result = await runner.run_stage(issue, Stage.SPEC, str(artifacts_path))

    assert result.passed is True
    assert result.stage == Stage.SPEC
    mock_agent.run.assert_called_once()


async def test_harness_halts_on_failure():
    mock_agent = AsyncMock()
    mock_agent.run.return_value = AgentResult(exit_code=1, stdout="", stderr="agent crashed")

    runner = HarnessRunner(agent_factory=_mock_factory(mock_agent), repo_path="/tmp/testrepo")

    issue = Issue(
        id="SUP-001",
        title="Add rate limiting",
        filepath=".superseded/issues/SUP-001-add-rate-limiting.md",
    )
    with tempfile.TemporaryDirectory() as tmp:
        artifacts_path = Path(tmp) / ".superseded" / "artifacts" / "SUP-001"
        artifacts_path.mkdir(parents=True)
        result = await runner.run_stage(issue, Stage.BUILD, str(artifacts_path))

    assert result.passed is False
    assert "agent crashed" in result.error


def test_prompts_contain_agent_skills_content():
    spec_prompt = get_prompt_for_stage(Stage.SPEC)
    assert "spec-driven-development" in spec_prompt.lower() or "objective" in spec_prompt.lower()
    assert "boundaries" in spec_prompt.lower()

    plan_prompt = get_prompt_for_stage(Stage.PLAN)
    assert "task" in plan_prompt.lower() and "acceptance criteria" in plan_prompt.lower()

    build_prompt = get_prompt_for_stage(Stage.BUILD)
    assert "incremental" in build_prompt.lower() or "slice" in build_prompt.lower()

    verify_prompt = get_prompt_for_stage(Stage.VERIFY)
    assert "test" in verify_prompt.lower() and "failing" in verify_prompt.lower()

    review_prompt = get_prompt_for_stage(Stage.REVIEW)
    assert "correctness" in review_prompt.lower() and "security" in review_prompt.lower()

    ship_prompt = get_prompt_for_stage(Stage.SHIP)
    assert "commit" in ship_prompt.lower() and "atomic" in ship_prompt.lower()
