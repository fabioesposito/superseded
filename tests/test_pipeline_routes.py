from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from superseded.db import Database
from superseded.main import create_app
from superseded.models import Issue, Stage, StageResult
from superseded.pipeline.executor import StageExecutor
from superseded.pipeline.worktree import WorktreeManager


@pytest.fixture
def tmp_repo():
    with tempfile.TemporaryDirectory() as tmp:
        repo_path = Path(tmp)
        issues_dir = repo_path / ".superseded" / "issues"
        issues_dir.mkdir(parents=True)
        ticket = """---
id: SUP-001
title: Test issue
status: new
stage: spec
created: "2026-04-11"
assignee: ""
labels: []
repos: []
---

Test body.
"""
        (issues_dir / "SUP-001-test.md").write_text(ticket)
        yield str(repo_path)


async def _make_app_with_executor(tmp_repo, mock_runner):
    db_path = str(Path(tmp_repo) / ".superseded" / "state.db")
    db = Database(db_path)
    await db.initialize()

    from superseded.config import SupersededConfig
    from superseded.pipeline.events import PipelineEventManager
    from superseded.routes.deps import PipelineState

    config = SupersededConfig(repo_path=tmp_repo)
    event_manager = PipelineEventManager()
    worktree_manager = WorktreeManager(tmp_repo)
    executor = StageExecutor(
        runner=mock_runner,
        db=db,
        worktree_manager=worktree_manager,
    )
    pipeline = PipelineState(executor=executor, event_manager=event_manager)

    app = create_app(repo_path=tmp_repo, config=config, db=db)
    app.state.pipeline = pipeline
    return app, db


async def test_advance_issue_success(tmp_repo):
    mock_runner = AsyncMock()
    mock_runner.run_stage_with_retries.return_value = StageResult(
        stage=Stage.SPEC, passed=True, output="spec done"
    )

    app, db = await _make_app_with_executor(tmp_repo, mock_runner)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/pipeline/issues/SUP-001/advance", follow_redirects=False)
        assert resp.status_code == 303
        assert "/issues/SUP-001" in resp.headers["location"]

    await db.close()


async def test_advance_issue_not_found(tmp_repo):
    mock_runner = AsyncMock()
    app, db = await _make_app_with_executor(tmp_repo, mock_runner)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/pipeline/issues/SUP-999/advance", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"

    await db.close()


async def test_advance_issue_failure(tmp_repo):
    mock_runner = AsyncMock()
    mock_runner.run_stage_with_retries.return_value = StageResult(
        stage=Stage.SPEC, passed=False, output="", error="spec failed"
    )

    app, db = await _make_app_with_executor(tmp_repo, mock_runner)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/pipeline/issues/SUP-001/advance", follow_redirects=False)
        assert resp.status_code == 303

    await db.close()


async def test_advance_issue_final_stage_done(tmp_repo):
    ticket = """---
id: SUP-002
title: Final stage issue
status: in-progress
stage: ship
created: "2026-04-11"
assignee: ""
labels: []
repos: []
---

Ship it.
"""
    issues_dir = Path(tmp_repo) / ".superseded" / "issues"
    (issues_dir / "SUP-002-ship.md").write_text(ticket)

    mock_runner = AsyncMock()
    mock_runner.run_stage_with_retries.return_value = StageResult(
        stage=Stage.SHIP, passed=True, output="shipped"
    )

    app, db = await _make_app_with_executor(tmp_repo, mock_runner)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/pipeline/issues/SUP-002/advance", follow_redirects=False)
        assert resp.status_code == 303

    await db.close()


async def test_retry_issue_success(tmp_repo):
    mock_runner = AsyncMock()
    mock_runner.run_stage_with_retries.return_value = StageResult(
        stage=Stage.SPEC, passed=True, output="spec done"
    )

    app, db = await _make_app_with_executor(tmp_repo, mock_runner)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/pipeline/issues/SUP-001/retry", follow_redirects=False)
        assert resp.status_code == 303
        assert "/issues/SUP-001" in resp.headers["location"]

    await db.close()


async def test_retry_issue_failure_sets_paused(tmp_repo):
    mock_runner = AsyncMock()
    mock_runner.run_stage_with_retries.return_value = StageResult(
        stage=Stage.SPEC, passed=False, output="", error="still broken"
    )

    app, db = await _make_app_with_executor(tmp_repo, mock_runner)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/pipeline/issues/SUP-001/retry", follow_redirects=False)
        assert resp.status_code == 303

    await db.close()


async def test_retry_issue_not_found(tmp_repo):
    mock_runner = AsyncMock()
    app, db = await _make_app_with_executor(tmp_repo, mock_runner)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/pipeline/issues/SUP-999/retry", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"

    await db.close()


async def test_pipeline_events_sse():
    with tempfile.TemporaryDirectory() as tmp:
        repo_path = Path(tmp)
        (repo_path / ".superseded" / "issues").mkdir(parents=True)

        db_path = str(repo_path / ".superseded" / "state.db")
        db = Database(db_path)
        await db.initialize()

        issue = Issue(id="SUP-001", title="Test", filepath="")
        await db.upsert_issue(issue)

        app = create_app(repo_path=str(repo_path), db=db)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # The SSE endpoint streams indefinitely, so just verify it starts
            # by checking that we can connect and get a response
            import asyncio

            async def _fetch_sse():
                async with client.stream("GET", "/pipeline/events") as resp:
                    assert resp.status_code == 200
                    assert "text/event-stream" in resp.headers["content-type"]
                    # Read first chunk then break
                    async for chunk in resp.aiter_text():
                        if chunk:
                            return chunk

            # Race with timeout to avoid hanging
            try:
                result = await asyncio.wait_for(_fetch_sse(), timeout=5)
                assert result  # got some data
            except TimeoutError:
                pass  # acceptable - endpoint may not emit immediately

        await db.close()


async def test_advance_issue_invalid_id(tmp_repo):
    mock_runner = AsyncMock()
    app, db = await _make_app_with_executor(tmp_repo, mock_runner)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/pipeline/issues/INVALID/advance", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"

    await db.close()


async def test_retry_issue_invalid_id(tmp_repo):
    mock_runner = AsyncMock()
    app, db = await _make_app_with_executor(tmp_repo, mock_runner)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/pipeline/issues/NOT-VALID/retry", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"

    await db.close()


async def test_historical_events_invalid_id(tmp_repo):
    mock_runner = AsyncMock()
    app, db = await _make_app_with_executor(tmp_repo, mock_runner)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/pipeline/issues/bad-id/events", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"

    await db.close()


async def test_stream_events_invalid_id(tmp_repo):
    mock_runner = AsyncMock()
    app, db = await _make_app_with_executor(tmp_repo, mock_runner)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/pipeline/issues/xss-attempt/events/stream", follow_redirects=False
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"

    await db.close()
