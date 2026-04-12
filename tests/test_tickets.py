import tempfile
from pathlib import Path

from superseded.models import IssueStatus, Stage
from superseded.tickets.reader import list_issues, read_issue
from superseded.tickets.writer import update_issue_status, write_issue

SAMPLE_TICKET = """---
id: SUP-001
title: Add rate limiting
status: new
stage: spec
created: "2026-04-11"
assignee: ""
labels:
  - backend
---

## Description
Add rate limiting to the API.

## Acceptance Criteria
- [ ] Rate limiter middleware added
- [ ] Configurable limits per endpoint
"""


def test_write_and_read_issue():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        issues_dir = repo / ".superseded" / "issues"
        issues_dir.mkdir(parents=True)

        filepath = str(issues_dir / "SUP-001-add-rate-limiting.md")
        write_issue(filepath, SAMPLE_TICKET)

        assert Path(filepath).exists()
        issue = read_issue(filepath)
        assert issue.id == "SUP-001"
        assert issue.title == "Add rate limiting"
        assert issue.stage == Stage.SPEC


def test_list_issues():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        issues_dir = repo / ".superseded" / "issues"
        issues_dir.mkdir(parents=True)

        write_issue(str(issues_dir / "SUP-001-add-rate-limiting.md"), SAMPLE_TICKET)
        write_issue(
            str(issues_dir / "SUP-002-fix-bug.md"),
            SAMPLE_TICKET.replace("SUP-001", "SUP-002").replace("Add rate limiting", "Fix bug"),
        )

        issues = list_issues(str(issues_dir))
        assert len(issues) == 2
        ids = {i.id for i in issues}
        assert "SUP-001" in ids
        assert "SUP-002" in ids


def test_list_issues_empty_dir():
    with tempfile.TemporaryDirectory() as tmp:
        issues_dir = Path(tmp) / "issues"
        issues_dir.mkdir()
        issues = list_issues(str(issues_dir))
        assert issues == []


def test_write_issue_updates_frontmatter():
    with tempfile.TemporaryDirectory() as tmp:
        issues_dir = Path(tmp) / ".superseded" / "issues"
        issues_dir.mkdir(parents=True)
        filepath = str(issues_dir / "SUP-001-add-rate-limiting.md")

        write_issue(filepath, SAMPLE_TICKET)
        issue = read_issue(filepath)
        assert issue.status == IssueStatus.NEW

        update_issue_status(filepath, IssueStatus.IN_PROGRESS, Stage.BUILD)
        updated = read_issue(filepath)
        assert updated.status == IssueStatus.IN_PROGRESS
        assert updated.stage == Stage.BUILD
