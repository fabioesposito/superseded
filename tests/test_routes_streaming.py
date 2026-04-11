from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from superseded.db import Database
from superseded.main import create_app
from superseded.models import AgentEvent, Issue, Stage


@pytest.fixture
async def client():
    with tempfile.TemporaryDirectory() as tmp:
        repo_path = Path(tmp)
        (repo_path / ".superseded" / "issues").mkdir(parents=True)
        (repo_path / ".superseded" / "artifacts").mkdir(parents=True)

        db_path = str(repo_path / ".superseded" / "state.db")
        db = Database(db_path)
        await db.initialize()

        issue = Issue(
            id="SUP-001",
            title="Test",
            filepath=str(repo_path / ".superseded" / "issues" / "SUP-001-test.md"),
        )
        await db.upsert_issue(issue)

        await db.save_agent_event(
            "SUP-001",
            AgentEvent(event_type="stdout", content="past output", stage=Stage.BUILD),
        )

        app = create_app(repo_path=str(repo_path), db=db)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

        await db.close()


async def test_historical_events_endpoint(client):
    resp = await client.get("/pipeline/issues/SUP-001/events")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["content"] == "past output"


async def test_historical_events_empty_for_unknown(client):
    resp = await client.get("/pipeline/issues/SUP-999/events")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 0
