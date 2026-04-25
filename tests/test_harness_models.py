import pytest
from pydantic import ValidationError

from superseded.models import AgentContext, HarnessIteration, Issue, Stage


def test_harness_iteration_rejects_invalid_stage():
    with pytest.raises(ValidationError):
        HarnessIteration(attempt=0, stage="deploy")


def test_harness_iteration_serializes_and_deserializes():
    hi = HarnessIteration(
        attempt=2,
        stage=Stage.VERIFY,
        previous_errors=["timeout", "test failure"],
    )
    raw = hi.model_dump_json()
    restored = HarnessIteration.model_validate_json(raw)
    assert restored.attempt == 2
    assert restored.stage == Stage.VERIFY
    assert restored.previous_errors == ["timeout", "test failure"]


def test_agent_context_defaults_worktree_and_iteration():
    ctx = AgentContext(
        repo_path="/tmp/repo",
        issue=Issue(id="SUP-001", title="Test", filepath=".superseded/issues/SUP-001-test.md"),
        skill_prompt="Build this",
    )
    assert ctx.worktree_path == ""
    assert ctx.iteration == 0
    assert ctx.previous_errors == []
    assert ctx.artifacts_path == ""


def test_agent_context_serializes_and_deserializes():
    ctx = AgentContext(
        repo_path="/tmp/repo",
        issue=Issue(id="SUP-001", title="Test", filepath=".superseded/issues/SUP-001-test.md"),
        skill_prompt="Build this",
        artifacts_path=".superseded/artifacts/SUP-001",
        worktree_path="/tmp/repo/.superseded/worktrees/SUP-001",
        iteration=1,
        previous_errors=["build failed"],
    )
    raw = ctx.model_dump_json()
    restored = AgentContext.model_validate_json(raw)
    assert restored.worktree_path == "/tmp/repo/.superseded/worktrees/SUP-001"
    assert restored.iteration == 1
    assert restored.previous_errors == ["build failed"]
    assert restored.artifacts_path == ".superseded/artifacts/SUP-001"
