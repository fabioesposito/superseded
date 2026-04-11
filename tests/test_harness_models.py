from __future__ import annotations

from superseded.models import HarnessIteration, AgentContext, Issue, Stage


def test_harness_iteration_defaults():
    hi = HarnessIteration(
        attempt=0,
        stage=Stage.BUILD,
    )
    assert hi.attempt == 0
    assert hi.stage == Stage.BUILD
    assert hi.previous_errors == []


def test_harness_iteration_with_errors():
    hi = HarnessIteration(
        attempt=2,
        stage=Stage.VERIFY,
        previous_errors=["timeout", "test failure"],
    )
    assert hi.attempt == 2
    assert len(hi.previous_errors) == 2


def test_agent_context_has_new_fields():
    ctx = AgentContext(
        repo_path="/tmp/repo",
        issue=Issue(
            id="SUP-001", title="Test", filepath=".superseded/issues/SUP-001-test.md"
        ),
        skill_prompt="Build this",
        artifacts_path=".superseded/artifacts/SUP-001",
    )
    assert ctx.worktree_path == ""
    assert ctx.iteration == 0
    assert ctx.previous_errors == []


def test_agent_context_with_worktree():
    ctx = AgentContext(
        repo_path="/tmp/repo",
        issue=Issue(
            id="SUP-001", title="Test", filepath=".superseded/issues/SUP-001-test.md"
        ),
        skill_prompt="Build this",
        artifacts_path=".superseded/artifacts/SUP-001",
        worktree_path="/tmp/repo/.superseded/worktrees/SUP-001",
        iteration=1,
        previous_errors=["build failed"],
    )
    assert ctx.worktree_path == "/tmp/repo/.superseded/worktrees/SUP-001"
    assert ctx.iteration == 1
    assert ctx.previous_errors == ["build failed"]
