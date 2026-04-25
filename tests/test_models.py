import pytest
from pydantic import ValidationError

from superseded.models import (
    AgentResult,
    Issue,
    IssueStatus,
    Stage,
    StageResult,
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
    issue = Issue.from_frontmatter(content, filepath=".superseded/issues/SUP-001-refactor-auth.md")
    assert issue.id == "SUP-001"
    assert issue.title == "Refactor auth module"
    assert issue.status == IssueStatus.IN_PROGRESS
    assert issue.stage == Stage.BUILD
    assert issue.assignee == "claude-code"
    assert "backend" in issue.labels


def test_issue_defaults():
    issue = Issue(id="SUP-999", title="Test issue", filepath=".superseded/issues/SUP-999-test.md")
    assert issue.status == IssueStatus.NEW
    assert issue.stage == Stage.SPEC
    assert issue.assignee == ""


def test_stage_result_rejects_invalid_stage():
    with pytest.raises(ValidationError):
        StageResult(stage="deploy", passed=True)


def test_agent_result_serializes_and_deserializes():
    result = AgentResult(exit_code=0, stdout="ok", stderr="", files_changed=["src/main.py"])
    raw = result.model_dump_json()
    restored = AgentResult.model_validate_json(raw)
    assert restored.exit_code == 0
    assert restored.stdout == "ok"
    assert restored.files_changed == ["src/main.py"]


def test_issue_from_frontmatter_invalid_stage_defaults_to_spec():
    content = """---
id: SUP-001
title: Test
stage: invalid-stage
---
"""
    issue = Issue.from_frontmatter(content, filepath="/tmp/test.md")
    assert issue.stage == Stage.SPEC


def test_issue_from_frontmatter_invalid_status_defaults_to_new():
    content = """---
id: SUP-001
title: Test
status: unknown-status
---
"""
    issue = Issue.from_frontmatter(content, filepath="/tmp/test.md")
    assert issue.status == IssueStatus.NEW


def test_issue_next_stage():
    issue = Issue(id="SUP-001", title="Test", stage=Stage.SPEC)
    assert issue.next_stage() == Stage.PLAN

    issue = Issue(id="SUP-002", title="Test", stage=Stage.SHIP)
    assert issue.next_stage() is None


def test_issue_from_frontmatter_with_repos():
    content = """---
id: SUP-001
title: Cross-repo feature
repos:
  - frontend
  - backend
---

Body text
"""
    issue = Issue.from_frontmatter(content, filepath="/tmp/test.md")
    assert issue.repos == ["frontend", "backend"]


def test_issue_from_frontmatter_no_repos():
    content = """---
id: SUP-001
title: Single repo feature
---

Body text
"""
    issue = Issue.from_frontmatter(content, filepath="/tmp/test.md")
    assert issue.repos == []
