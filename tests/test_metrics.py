from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from superseded.db import Database
from superseded.main import create_app
from superseded.models import Issue, IssueStatus, Stage, StageResult


@pytest.fixture
async def client():
    with tempfile.TemporaryDirectory() as tmp:
        repo_path = Path(tmp)
        (repo_path / ".superseded" / "issues").mkdir(parents=True)
        (repo_path / ".superseded" / "artifacts").mkdir(parents=True)

        db_path = str(repo_path / ".superseded" / "state.db")
        db = Database(db_path)
        await db.initialize()

        for i in range(3):
            issue = Issue(
                id=f"SUP-{i:03d}",
                title=f"Issue {i}",
                filepath="",
                status=IssueStatus.DONE if i < 2 else IssueStatus.IN_PROGRESS,
            )
            await db.upsert_issue(issue)

        await db.save_stage_result(
            "SUP-000",
            StageResult(stage=Stage.BUILD, passed=True, output="ok"),
        )
        await db.save_stage_result(
            "SUP-001",
            StageResult(stage=Stage.BUILD, passed=False, output="", error="failed"),
        )

        app = create_app(repo_path=str(repo_path), db=db)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

        await db.close()


async def test_metrics_endpoint_returns_json(client):
    resp = await client.get("/pipeline/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_issues"] == 3
    assert "issues_by_status" in data
    assert "stage_success_rates" in data
    assert data["stage_success_rates"]["build"] == 0.5


async def test_metrics_dashboard_renders(client):
    resp = await client.get("/pipeline/metrics/dashboard")
    assert resp.status_code == 200
    assert "Pipeline Metrics" in resp.text
