from __future__ import annotations

import datetime
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from superseded.db import Database
from superseded.main import create_app
from superseded.models import Issue, Stage, StageResult
from superseded.notifications import NotificationService
from superseded.pipeline.executor import StageExecutor
from superseded.pipeline.worktree import WorktreeManager


@pytest.fixture
async def app_client():
    with tempfile.TemporaryDirectory() as tmp:
        repo_path = Path(tmp)
        (repo_path / ".superseded" / "issues").mkdir(parents=True)
        (repo_path / ".superseded" / "artifacts").mkdir(parents=True)

        db_path = str(repo_path / ".superseded" / "state.db")
        db = Database(db_path)
        await db.initialize()

        app = create_app(repo_path=str(repo_path), db=db)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac, db, str(repo_path)

        await db.close()


async def test_issue_detail_shows_duration(app_client):
    client, db, repo_path = app_client

    issue = Issue(id="SUP-001", title="Duration Issue", filepath="")
    await db.upsert_issue(issue)

    ticket_path = Path(repo_path) / ".superseded" / "issues" / "SUP-001-duration.md"
    ticket_path.write_text(
        "---\nid: SUP-001\ntitle: Duration Issue\nstatus: new\nstage: build\n---\nBody\n"
    )

    started = datetime.datetime(2026, 1, 1, 12, 0, 0)
    finished = datetime.datetime(2026, 1, 1, 12, 2, 34)
    result = StageResult(
        stage=Stage.BUILD,
        passed=True,
        output="ok",
        started_at=started,
        finished_at=finished,
    )
    await db.save_stage_result("SUP-001", result)

    response = await client.get("/issues/SUP-001")
    assert response.status_code == 200
    assert "2m 34s" in response.text


async def test_metrics_endpoint_includes_durations(app_client):
    client, db, _ = app_client

    issue = Issue(id="SUP-001", title="Metrics Duration", filepath="")
    await db.upsert_issue(issue)

    started = datetime.datetime(2026, 1, 1, 12, 0, 0)
    finished = datetime.datetime(2026, 1, 1, 12, 0, 5)
    result = StageResult(
        stage=Stage.BUILD,
        passed=True,
        output="ok",
        started_at=started,
        finished_at=finished,
    )
    await db.save_stage_result("SUP-001", result)

    resp = await client.get("/api/pipeline/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert "avg_stage_duration_ms" in data
    assert "build" in data["avg_stage_duration_ms"]
    assert data["avg_stage_duration_ms"]["build"] > 0


async def test_settings_notifications_page_renders(app_client):
    client, _, _ = app_client

    response = await client.get("/settings")
    assert response.status_code == 200
    assert "Notifications" in response.text


async def test_settings_notifications_update(app_client):
    client, _, _ = app_client

    await client.get("/")
    token = client.cookies.get("csrf_token", "")

    resp = await client.post(
        "/settings/notifications",
        data={"enabled": "1", "ntfy_topic": "test-topic"},
        headers={"X-CSRF-Token": token},
    )
    assert resp.status_code == 200
    assert "Active" in resp.text

    resp2 = await client.get("/settings")
    assert resp2.status_code == 200
    assert "test-topic" in resp2.text


async def test_notification_service_not_called_when_disabled():
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        service = NotificationService(enabled=False, topic="test-topic")
        await service.notify(title="test", message="msg", priority="default", tags=[])
        mock_post.assert_not_called()


async def test_notification_service_called_on_stage_pass():
    with tempfile.TemporaryDirectory() as tmp:
        repo_path = Path(tmp)
        (repo_path / ".superseded" / "issues").mkdir(parents=True)

        db_path = str(repo_path / ".superseded" / "state.db")
        db = Database(db_path)
        await db.initialize()

        mock_runner = AsyncMock()
        mock_runner.run_stage_streaming.return_value = StageResult(
            stage=Stage.SPEC, passed=True, output="spec done"
        )

        notification_service = NotificationService(enabled=True, topic="test")

        worktree_manager = WorktreeManager(str(repo_path))
        executor = StageExecutor(
            runner=mock_runner,
            db=db,
            worktree_manager=worktree_manager,
            notification_service=notification_service,
        )

        ticket_path = repo_path / ".superseded" / "issues" / "SUP-001-test.md"
        ticket_path.write_text(
            "---\nid: SUP-001\ntitle: Test\nstatus: new\nstage: spec\n---\nBody\n"
        )

        issue = Issue(id="SUP-001", title="Test", filepath=str(ticket_path))
        await db.upsert_issue(issue)

        with patch.object(notification_service, "notify", new_callable=AsyncMock) as mock_notify:
            from superseded.config import SupersededConfig

            config = SupersededConfig(repo_path=str(repo_path))
            await executor.run_stage(issue, Stage.SPEC, config)
            mock_notify.assert_called_once()

        await db.close()
