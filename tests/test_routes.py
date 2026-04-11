import tempfile
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

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
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/")
        assert response.status_code == 200
        assert "Superseded" in response.text


async def test_dashboard_shows_issues(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/")
        assert response.status_code == 200
        assert "SUP-001" in response.text


async def test_issue_detail_page(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/issues/SUP-001")
        assert response.status_code == 200
        assert "SUP-001" in response.text


async def test_new_issue_form(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/issues/new")
        assert response.status_code == 200
        assert "New Issue" in response.text


async def test_create_issue(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/issues/new",
            data={
                "title": "My new feature",
                "body": "Add a cool feature",
                "labels": "frontend",
                "assignee": "claude-code",
            },
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
    from unittest.mock import AsyncMock, patch

    from superseded.config import RepoEntry, SupersededConfig
    from superseded.models import Stage, StageResult
    from superseded.routes.pipeline import _run_stage
    from superseded.routes.deps import Deps

    app = create_app(repo_path=tmp_multi_repo)
    await app.state.db.initialize()

    config = SupersededConfig(repo_path=tmp_multi_repo)
    deps = Deps(config=config, db=app.state.db)

    mock_result = StageResult(stage=Stage.SPEC, passed=True, output="ok")

    with patch("superseded.routes.pipeline._get_harness_runner") as mock_get_runner:
        mock_runner = AsyncMock()
        mock_runner.run_stage_with_retries.return_value = mock_result
        mock_get_runner.return_value = mock_runner

        result = await _run_stage(deps, "SUP-002", Stage.SPEC)

        assert result.passed is True
        assert "[frontend]" in result.output
        assert "[backend]" in result.output

        calls = mock_runner.run_stage_with_retries.call_args_list
        assert len(calls) == 2
        repos_called = {c.kwargs.get("repo") for c in calls}
        assert repos_called == {"frontend", "backend"}


async def test_run_stage_single_repo_backward_compat(tmp_multi_repo):
    from unittest.mock import AsyncMock, patch

    from superseded.models import Stage, StageResult
    from superseded.routes.pipeline import _run_stage
    from superseded.routes.deps import Deps

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
---

Single repo body.
"""
    )

    from superseded.config import SupersededConfig

    config = SupersededConfig(repo_path=tmp_multi_repo)
    deps = Deps(config=config, db=app.state.db)

    mock_result = StageResult(stage=Stage.SPEC, passed=True, output="ok")

    with patch("superseded.routes.pipeline._get_harness_runner") as mock_get_runner:
        mock_runner = AsyncMock()
        mock_runner.run_stage_with_retries.return_value = mock_result
        mock_get_runner.return_value = mock_runner

        result = await _run_stage(deps, "SUP-003", Stage.SPEC)

        assert result.passed is True
        assert "[primary]" in result.output

        calls = mock_runner.run_stage_with_retries.call_args_list
        assert len(calls) == 1
        assert calls[0].kwargs.get("repo") is None
