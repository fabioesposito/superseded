"""End-to-end integration tests: create repo, init superseded, create ticket, verify flow."""

import tempfile
from pathlib import Path

from unittest.mock import AsyncMock

from superseded.config import load_config
from superseded.db import Database
from superseded.models import AgentResult, Issue, IssueStatus, Stage
from superseded.pipeline.context import ContextAssembler
from superseded.pipeline.harness import HarnessRunner
from superseded.pipeline.plan import PlanTask, write_plan, read_plan
from superseded.pipeline.worktree import WorktreeManager
from superseded.tickets.reader import list_issues, read_issue
from superseded.tickets.writer import write_issue, update_issue_status


SAMPLE_TICKET = """---
id: SUP-001
title: Integrate payment API
status: new
stage: spec
created: "2026-04-11"
assignee: claude-code
labels:
  - backend
  - integration
---

## Description
Integrate the payment gateway API into the checkout flow.

## Acceptance Criteria
- [ ] Payment API client created
- [ ] Checkout flow updated
- [ ] Tests for happy path and failures
"""


async def test_full_ticket_lifecycle():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        config_dir = repo / ".superseded"
        config_dir.mkdir()
        issues_dir = config_dir / "issues"
        issues_dir.mkdir()

        config = load_config(repo)
        assert config.default_agent == "claude-code"

        filepath = str(issues_dir / "SUP-001-integrate-payment-api.md")
        write_issue(filepath, SAMPLE_TICKET)

        issue = read_issue(filepath)
        assert issue.id == "SUP-001"
        assert issue.title == "Integrate payment API"
        assert issue.stage == Stage.SPEC
        assert issue.status == IssueStatus.NEW

        update_issue_status(filepath, IssueStatus.IN_PROGRESS, Stage.PLAN)
        updated = read_issue(filepath)
        assert updated.status == IssueStatus.IN_PROGRESS
        assert updated.stage == Stage.PLAN

        next_stage = updated.next_stage()
        assert next_stage == Stage.BUILD

        db = Database(str(config_dir / "state.db"))
        await db.initialize()
        await db.upsert_issue(issue)
        fetched = await db.get_issue("SUP-001")
        assert fetched["title"] == "Integrate payment API"

        await db.close()


async def test_list_issues_across_multiple():
    with tempfile.TemporaryDirectory() as tmp:
        issues_dir = Path(tmp) / "issues"
        issues_dir.mkdir(parents=True)

        for i in range(1, 4):
            content = SAMPLE_TICKET.replace("SUP-001", f"SUP-00{i}").replace(
                "Integrate payment API", f"Issue {i}"
            )
            write_issue(str(issues_dir / f"SUP-00{i}-issue-{i}.md"), content)

        issues = list_issues(str(issues_dir))
        assert len(issues) == 3


def test_pipeline_prompts_load_from_skills():
    from superseded.pipeline.prompts import get_prompt_for_stage, AGENT_SKILLS_DIR

    for stage in Stage:
        prompt = get_prompt_for_stage(stage)
        assert len(prompt) > 50, f"Stage {stage} has no prompt"

    if AGENT_SKILLS_DIR.exists():
        spec_prompt = get_prompt_for_stage(Stage.SPEC)
        assert "spec" in spec_prompt.lower()


def test_stage_definitions_match_prompts():
    from superseded.pipeline.stages import STAGE_DEFINITIONS
    from superseded.pipeline.prompts import get_prompt_for_stage

    for stage_def in STAGE_DEFINITIONS:
        prompt = get_prompt_for_stage(stage_def.stage)
        assert len(prompt) > 0, f"No prompt for stage {stage_def.stage}"


import subprocess


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(path), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(path),
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=str(path), capture_output=True
    )
    (path / "README.md").write_text("test repo")
    subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), capture_output=True)


async def test_harness_full_lifecycle():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _init_git_repo(repo)
        config_dir = repo / ".superseded"
        config_dir.mkdir()
        issues_dir = config_dir / "issues"
        issues_dir.mkdir()
        artifacts_dir = config_dir / "artifacts"
        artifacts_dir.mkdir()

        config = load_config(repo)
        assert config.max_retries == 3

        filepath = str(issues_dir / "SUP-001-test-issue.md")
        write_issue(filepath, SAMPLE_TICKET)

        issue = read_issue(filepath)
        assert issue.stage == Stage.SPEC

        mock_agent = AsyncMock()
        mock_agent.run.return_value = AgentResult(
            exit_code=0, stdout="spec written", stderr=""
        )

        runner = HarnessRunner(agent=mock_agent, repo_path=str(repo), max_retries=3)
        result = await runner.run_stage_with_retries(
            issue=issue,
            stage=Stage.SPEC,
            artifacts_path=str(artifacts_dir / "SUP-001"),
        )

        assert result.passed is True
        assert mock_agent.run.call_count == 1


async def test_context_assembler_includes_artifacts():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _init_git_repo(repo)

        artifacts_dir = Path(tmp) / ".superseded" / "artifacts" / "SUP-001"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "spec.md").write_text("# Spec\nDetailed spec for the feature.")

        issue = Issue(
            id="SUP-001", title="Test", filepath=".superseded/issues/SUP-001-test.md"
        )
        assembler = ContextAssembler(repo_path=str(repo))
        prompt = assembler.build(
            stage=Stage.PLAN,
            issue=issue,
            artifacts_path=str(artifacts_dir),
        )

        assert "Spec" in prompt
        assert "spec.md" in prompt.lower()


async def test_worktree_lifecycle():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _init_git_repo(repo)

        wm = WorktreeManager(str(repo))
        worktree_path = await wm.create("SUP-TEST")
        assert worktree_path.exists()
        assert wm.exists("SUP-TEST")
        await wm.cleanup("SUP-TEST")
        assert not wm.exists("SUP-TEST")
