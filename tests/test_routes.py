import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from superseded.main import create_app

SAMPLE_TICKET = """---
id: SUP-001
title: Test issue
status: new
stage: spec
created: "2026-04-11"
assignee: ""
labels: []
---

Test body.
"""


@pytest.fixture
def tmp_repo():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        issues_dir = repo / ".superseded" / "issues"
        issues_dir.mkdir(parents=True)
        (issues_dir / "SUP-001-test.md").write_text(SAMPLE_TICKET)
        yield str(repo)


async def test_dashboard_loads(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/")
        assert response.status_code == 200
        assert "Superseded" in response.text


async def test_dashboard_shows_issues(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/")
        assert response.status_code == 200
        assert "SUP-001" in response.text


async def test_issue_detail_page(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/issues/SUP-001")
        assert response.status_code == 200
        assert "SUP-001" in response.text


async def test_new_issue_form(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/issues/new")
        assert response.status_code == 200
        assert "New Issue" in response.text


async def test_create_issue(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/")
        token = client.cookies.get("csrf_token", "")
        response = await client.post(
            "/issues/new",
            data={
                "title": "My new feature",
                "body": "Add a cool feature",
                "labels": "frontend",
                "assignee": "claude-code",
            },
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
        assert response.status_code == 303


MULTI_REPO_TICKET = """---
id: SUP-002
title: Multi-repo issue
status: new
stage: spec
created: "2026-04-11"
assignee: ""
labels: []
repos:
  - frontend
  - backend
---

Test body for multi-repo.
"""


@pytest.fixture
def tmp_multi_repo():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        issues_dir = repo / ".superseded" / "issues"
        issues_dir.mkdir(parents=True)
        (issues_dir / "SUP-002-multi.md").write_text(MULTI_REPO_TICKET)
        (repo / ".git").mkdir()
        yield str(repo)


async def test_run_stage_multi_repo_fans_out(tmp_multi_repo):
    from unittest.mock import AsyncMock

    from superseded.agents.factory import AgentFactory
    from superseded.models import AgentEvent, Stage
    from superseded.pipeline.events import PipelineEventManager
    from superseded.pipeline.executor import StageExecutor
    from superseded.pipeline.worktree import WorktreeManager

    app = create_app(repo_path=tmp_multi_repo)
    await app.state.db.initialize()

    mock_runner = AsyncMock()
    mock_runner.repo_path = tmp_multi_repo
    mock_runner.agent_factory = AgentFactory()
    mock_runner.stage_configs = {}
    mock_runner.event_manager = PipelineEventManager()

    worktree_manager = WorktreeManager(tmp_multi_repo)
    executor = StageExecutor(
        runner=mock_runner,
        db=app.state.db,
        worktree_manager=worktree_manager,
    )

    call_count = 0

    def mock_resolve(stage):
        nonlocal call_count
        call_count += 1
        mock_agent = AsyncMock()

        async def fake_stream(prompt, context):
            yield AgentEvent(
                event_type="stdout",
                content="ok with sufficient content to pass the minimum output character requirement",
                stage=Stage.SPEC,
            )
            yield AgentEvent(
                event_type="status",
                content="",
                stage=Stage.SPEC,
                metadata={"exit_code": 0, "duration_ms": 100},
            )

        mock_agent.run_streaming = fake_stream
        return mock_agent

    executor._harness.resolve_agent = mock_resolve

    issues_dir = Path(tmp_multi_repo) / ".superseded" / "issues"
    from superseded.tickets.reader import list_issues

    issues = list_issues(str(issues_dir))
    issue = issues[0]

    result = await executor.run_stage(issue, Stage.SPEC, app.state.config)

    assert result.passed is True
    assert "[frontend]" in result.output
    assert "[backend]" in result.output
    assert call_count == 2


async def test_run_stage_single_repo_backward_compat(tmp_multi_repo):
    from unittest.mock import AsyncMock

    from superseded.agents.factory import AgentFactory
    from superseded.models import AgentEvent, Stage
    from superseded.pipeline.events import PipelineEventManager
    from superseded.pipeline.executor import StageExecutor
    from superseded.pipeline.worktree import WorktreeManager
    from superseded.tickets.reader import list_issues

    app = create_app(repo_path=tmp_multi_repo)
    await app.state.db.initialize()

    issues_dir = Path(tmp_multi_repo) / ".superseded" / "issues"
    (issues_dir / "SUP-003-single.md").write_text(
        """---
id: SUP-003
title: Single repo issue
status: new
stage: spec
created: "2026-04-11"
assignee: ""
labels: []
repos: []
---

Single repo body.
"""
    )

    mock_runner = AsyncMock()
    mock_runner.repo_path = tmp_multi_repo
    mock_runner.agent_factory = AgentFactory()
    mock_runner.stage_configs = {}
    mock_runner.event_manager = PipelineEventManager()

    worktree_manager = WorktreeManager(tmp_multi_repo)
    executor = StageExecutor(
        runner=mock_runner,
        db=app.state.db,
        worktree_manager=worktree_manager,
    )

    mock_agent = AsyncMock()

    async def fake_stream(prompt, context):
        yield AgentEvent(
            event_type="stdout",
            content="ok with sufficient content to pass the minimum output character requirement",
            stage=Stage.SPEC,
        )
        yield AgentEvent(
            event_type="status",
            content="",
            stage=Stage.SPEC,
            metadata={"exit_code": 0, "duration_ms": 100},
        )

    mock_agent.run_streaming = fake_stream
    executor._harness.resolve_agent = lambda stage: mock_agent

    issues = list_issues(str(issues_dir))
    single_issue = next(i for i in issues if i.id == "SUP-003")

    result = await executor.run_stage(single_issue, Stage.SPEC, app.state.config)

    assert result.passed is True
    assert "[primary]" in result.output
