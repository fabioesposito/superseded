from superseded.models import (
    Issue,
    IssueStatus,
    Stage,
    StageResult,
    AgentResult,
    AgentContext,
)


def test_issue_from_frontmatter():
    content = """---
id: SUP-001
title: Refactor auth module
status: in-progress
stage: build
created: "2026-04-11"
assignee: claude-code
labels:
  - backend
  - security
---

## Description
Refactor the auth module.
"""
    issue = Issue.from_frontmatter(
        content, filepath=".superseded/issues/SUP-001-refactor-auth.md"
    )
    assert issue.id == "SUP-001"
    assert issue.title == "Refactor auth module"
    assert issue.status == IssueStatus.IN_PROGRESS
    assert issue.stage == Stage.BUILD
    assert issue.assignee == "claude-code"
    assert "backend" in issue.labels


def test_issue_defaults():
    issue = Issue(
        id="SUP-999", title="Test issue", filepath=".superseded/issues/SUP-999-test.md"
    )
    assert issue.status == IssueStatus.NEW
    assert issue.stage == Stage.SPEC
    assert issue.assignee == ""


def test_stage_result_pass():
    result = StageResult(
        stage=Stage.BUILD, passed=True, output="done", artifacts=["src/auth.py"]
    )
    assert result.passed is True
    assert result.stage == Stage.BUILD


def test_agent_result():
    result = AgentResult(
        exit_code=0, stdout="ok", stderr="", files_changed=["src/main.py"]
    )
    assert result.exit_code == 0
    assert len(result.files_changed) == 1


def test_agent_context():
    ctx = AgentContext(
        repo_path="/tmp/myrepo",
        issue=Issue(
            id="SUP-001", title="Test", filepath=".superseded/issues/SUP-001-test.md"
        ),
        skill_prompt="You are a planner...",
        artifacts_path=".superseded/artifacts/SUP-001",
    )
    assert ctx.repo_path == "/tmp/myrepo"
    assert ctx.skill_prompt == "You are a planner..."


def test_issue_next_stage():
    issue = Issue(id="SUP-001", title="Test", stage=Stage.SPEC)
    assert issue.next_stage() == Stage.PLAN

    issue = Issue(id="SUP-002", title="Test", stage=Stage.SHIP)
    assert issue.next_stage() is None
