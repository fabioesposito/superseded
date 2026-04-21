from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from superseded.github import GhComment, GhIssue
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


async def test_issue_detail_invalid_issue_id(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/issues/not-valid")
        assert resp.status_code == 400
        assert "Invalid issue ID" in resp.text


async def test_issue_detail_invalid_issue_id_path_traversal(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/issues/..%2Fetc%2Fpasswd")
        # FastAPI normalizes path traversal before routing, so this never
        # reaches the handler — it's blocked at the routing layer as 404.
        assert resp.status_code in (400, 404)


async def test_stage_detail_invalid_issue_id(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/issues/abc123/stage/spec")
        assert resp.status_code == 400
        assert "Invalid issue ID" in resp.text


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


async def _get_csrf(client):
    await client.get("/")
    return client.cookies.get("csrf_token", "")


async def test_create_issue_with_labels_and_repos(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await _get_csrf(client)
        resp = await client.post(
            "/issues/new",
            data={
                "title": "Multi-repo feature",
                "body": "Implement across repos",
                "labels": "frontend,backend",
                "assignee": "claude-code",
                "repos": "frontend,backend",
            },
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
        assert resp.status_code == 303


async def test_health_endpoint(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


async def test_import_github_issue_returns_form_partial(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()

    mock_issue = GhIssue(
        title="Fix login bug",
        body="The login page crashes.",
        labels=["bug", "priority-high"],
        assignee="claude-code",
        comments=[
            GhComment(author="alice", body="Reproduced.", created_at="2026-04-10T12:00:00Z"),
        ],
        url="https://github.com/owner/repo/issues/42",
    )

    with patch("superseded.routes.web.issues.fetch_github_issue", return_value=mock_issue):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            token = await _get_csrf(client)
            resp = await client.post(
                "/issues/import",
                data={"github_url": "https://github.com/owner/repo/issues/42"},
                headers={"X-CSRF-Token": token},
            )

    assert resp.status_code == 200
    assert "Fix login bug" in resp.text
    assert "The login page crashes." in resp.text
    assert "bug, priority-high" in resp.text
    assert "claude-code" in resp.text
    assert "@alice" in resp.text
    assert "https://github.com/owner/repo/issues/42" in resp.text


async def test_import_github_issue_invalid_url(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await _get_csrf(client)
        resp = await client.post(
            "/issues/import",
            data={"github_url": "https://not-github.com/foo"},
            headers={"X-CSRF-Token": token},
        )

    assert resp.status_code == 200
    assert "Invalid GitHub issue URL" in resp.text


async def test_create_issue_saves_github_url(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await _get_csrf(client)
        resp = await client.post(
            "/issues/new",
            data={
                "title": "Imported issue",
                "body": "From GitHub",
                "labels": "bug",
                "assignee": "",
                "repos": "",
                "github_url": "https://github.com/owner/repo/issues/42",
            },
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    issues_dir = Path(tmp_repo) / ".superseded" / "issues"
    md_files = list(issues_dir.glob("*imported-issue*.md"))
    assert len(md_files) == 1

    content = md_files[0].read_text()
    assert 'github_url: "https://github.com/owner/repo/issues/42"' in content


async def test_approve_issue(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()

    issue = Issue(id="SUP-001", title="Test", filepath="", pause_reason="awaiting-input", stage=Stage.PLAN)
    await app.state.db.upsert_issue(issue)

    artifacts_dir = Path(tmp_repo) / ".superseded" / "artifacts" / "SUP-001" / "primary"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "approval.md").write_text("Needs approval")

    with patch("superseded.routes.web.issues._run_and_advance") as mock_run:
        from fastapi.responses import HTMLResponse
        mock_run.return_value = HTMLResponse(content="advanced")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            token = await _get_csrf(client)
            resp = await client.post(
                "/issues/SUP-001/approve",
                headers={"X-CSRF-Token": token},
            )

    assert resp.status_code == 200
    assert not (artifacts_dir / "approval.md").exists()
    db_issue = await app.state.db.get_issue("SUP-001")
    assert db_issue["pause_reason"] == ""
    mock_run.assert_called_once()


async def test_reject_issue(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()

    issue = Issue(id="SUP-001", title="Test", filepath="", pause_reason="awaiting-input", stage=Stage.PLAN)
    await app.state.db.upsert_issue(issue)

    artifacts_dir = Path(tmp_repo) / ".superseded" / "artifacts" / "SUP-001" / "primary"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "approval.md").write_text("Needs approval")

    with patch("superseded.routes.web.issues._run_and_advance") as mock_run:
        from fastapi.responses import HTMLResponse
        mock_run.return_value = HTMLResponse(content="advanced")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            token = await _get_csrf(client)
            resp = await client.post(
                "/issues/SUP-001/reject",
                data={"feedback": "I don't like it"},
                headers={"X-CSRF-Token": token},
            )

    assert resp.status_code == 200
    assert not (artifacts_dir / "approval.md").exists()
    db_issue = await app.state.db.get_issue("SUP-001")
    assert db_issue["pause_reason"] == ""

    results = await app.state.db.get_stage_results("SUP-001")
    assert len(results) == 1
    assert "I don't like it" in results[0]["error"]

    mock_run.assert_called_once()
