from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from superseded.main import create_app
from superseded.models import HarnessIteration, Issue, Stage, StageResult

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


async def test_issue_detail_not_found(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/issues/SUP-999")
        assert resp.status_code == 404
        assert "Issue not found" in resp.text


async def test_issue_detail_with_stage_results(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()

    issue = Issue(id="SUP-001", title="Test", filepath="")
    await app.state.db.upsert_issue(issue)
    await app.state.db.save_stage_result(
        "SUP-001",
        StageResult(stage=Stage.SPEC, passed=True, output="spec done"),
    )
    await app.state.db.save_harness_iteration(
        "SUP-001",
        HarnessIteration(attempt=0, stage=Stage.SPEC),
        exit_code=0,
        output="spec done",
        error="",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/issues/SUP-001")
        assert resp.status_code == 200
        assert "SUP-001" in resp.text


async def test_issue_detail_groups_results_by_repo(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()

    issue = Issue(id="SUP-001", title="Test", filepath="")
    await app.state.db.upsert_issue(issue)
    await app.state.db.save_stage_result(
        "SUP-001",
        StageResult(stage=Stage.BUILD, passed=True, output="ok"),
        repo="primary",
    )
    await app.state.db.save_stage_result(
        "SUP-001",
        StageResult(stage=Stage.BUILD, passed=True, output="ok"),
        repo="frontend",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/issues/SUP-001")
        assert resp.status_code == 200


async def test_stage_detail_valid_stage(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()

    issue = Issue(id="SUP-001", title="Test", filepath="")
    await app.state.db.upsert_issue(issue)
    await app.state.db.save_stage_result(
        "SUP-001",
        StageResult(stage=Stage.SPEC, passed=True, output="spec done"),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/issues/SUP-001/stage/spec")
        assert resp.status_code == 200


async def test_stage_detail_invalid_stage(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/issues/SUP-001/stage/bogus")
        assert resp.status_code == 400
        assert "Invalid stage: bogus" in resp.text


async def test_stage_detail_issue_not_found(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/issues/SUP-999/stage/spec")
        assert resp.status_code == 404
        assert "Issue not found" in resp.text


async def test_stage_detail_no_result_for_stage(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()

    issue = Issue(id="SUP-001", title="Test", filepath="")
    await app.state.db.upsert_issue(issue)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/issues/SUP-001/stage/build")
        assert resp.status_code == 200


async def test_stage_detail_with_result(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()

    issue = Issue(id="SUP-001", title="Test", filepath="")
    await app.state.db.upsert_issue(issue)
    await app.state.db.save_stage_result(
        "SUP-001",
        StageResult(stage=Stage.BUILD, passed=False, output="", error="build failed"),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/issues/SUP-001/stage/build")
        assert resp.status_code == 200


async def test_create_issue_with_labels_and_repos(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/issues/new",
            data={
                "title": "Multi-repo feature",
                "body": "Implement across repos",
                "labels": "frontend,backend",
                "assignee": "claude-code",
                "repos": "frontend,backend",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303


async def test_health_endpoint(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
