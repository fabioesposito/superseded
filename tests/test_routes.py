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
