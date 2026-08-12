from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from superseded.memory.store import MemoryStore
from superseded.models import Finding, ReviewResult, ReviewUsage
from superseded.server.app import create_app
from superseded.server.client import review_via_server
from superseded.server.config import ServerConfig
from superseded.server.github import GitHubApp
from superseded.server.repo_manager import RepoManager
from superseded.server.worker import ReviewWorker


@pytest.fixture
def server(tmp_path):
    key_file = tmp_path / "key.pem"
    key_file.write_text("fake-key")
    config = ServerConfig(
        app_id=12345,
        webhook_secret="whsec_test",
        private_key_path=key_file,
        temp_dir=tmp_path / "repos",
        api_key="test-api-key",
    )
    github = GitHubApp(
        app_id=config.app_id,
        private_key_path=config.private_key_path,
        webhook_secret=config.webhook_secret,
    )
    repo_manager = RepoManager(base_path=config.temp_dir)
    worker = ReviewWorker(
        github=github, repo_manager=repo_manager, max_concurrent=1, provider=MagicMock()
    )
    store = MemoryStore(tmp_path / "memory.db")
    application = create_app(
        config=config, github=github, worker=worker, repo_manager=repo_manager, store=store
    )
    return SimpleNamespace(app=application, worker=worker, config=config, store=store)


@pytest.fixture
def client(server):
    return TestClient(server.app)


def _auth_headers():
    return {"Authorization": "Bearer test-api-key"}


def test_jobs_endpoint_unknown_job_404(client):
    r = client.get("/review/jobs/missing", headers=_auth_headers())
    assert r.status_code == 404


def test_jobs_endpoint_requires_auth(client):
    r = client.get("/review/jobs/anything")
    assert r.status_code == 401


def test_jobs_endpoint_501_when_no_api_key(tmp_path):
    key_file = tmp_path / "key.pem"
    key_file.write_text("fake-key")
    config = ServerConfig(
        app_id=1,
        webhook_secret="x",
        private_key_path=key_file,
        temp_dir=tmp_path / "r",
        api_key="",
    )
    github = GitHubApp(app_id=1, private_key_path=key_file, webhook_secret="x")
    repo_manager = RepoManager(base_path=config.temp_dir)
    worker = ReviewWorker(github=github, repo_manager=repo_manager, provider=MagicMock())
    store = MemoryStore(tmp_path / "m.db")
    app = create_app(
        config=config, github=github, worker=worker, repo_manager=repo_manager, store=store
    )
    r = TestClient(app).get("/review/jobs/x", headers={"Authorization": "Bearer anything"})
    assert r.status_code == 501


def test_jobs_endpoint_returns_queued(server, client):
    server.worker._record_job("job-1", "queued")
    r = client.get("/review/jobs/job-1", headers=_auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "queued"
    assert "result" not in data or data["result"] is None
    assert "error" not in data or data["error"] is None


def test_jobs_endpoint_returns_completed_with_result(server, client):
    result = ReviewResult(
        findings=[
            Finding(
                pass_name="security",
                severity="critical",
                file="a.py",
                line=3,
                title="T",
                description="D",
                suggestion="S",
            )
        ],
        warnings=["pass skipped"],
    )
    server.worker._record_job("job-2", "completed", result=result)
    r = client.get("/review/jobs/job-2", headers=_auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "completed"
    assert data["result"]["findings"][0]["file"] == "a.py"
    assert data["result"]["warnings"] == ["pass skipped"]
    assert data["error"] is None


def test_jobs_endpoint_returns_failed_with_error(server, client):
    server.worker._record_job("job-3", "failed", error="boom")
    r = client.get("/review/jobs/job-3", headers=_auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "failed"
    assert data["error"] == "boom"
    assert data["result"] is None


@pytest.mark.asyncio
async def test_client_server_roundtrip_end_to_end(server):
    """Real submit_review + poll_review against the real FastAPI app.

    Drives the actual client↔server wire format (including the
    tuple-as-list coercion of usage.per_pass through model_dump(mode="json")
    → endpoint → ReviewResult.model_validate) by injecting a TestClient
    (an httpx.Client subclass) as the sync HTTP client. httpx.ASGITransport
    only implements handle_async_request, so it cannot back a synchronous
    httpx.Client; TestClient bridges sync→ASGI internally.
    """
    await server.store.init()
    await server.store.record_installation(installation_id=99, owner="o", repos=["o/r", "r"])

    server.worker.github.resolve_installation = AsyncMock(return_value=99)
    server.worker.github.get_installation_token = AsyncMock(return_value="tok")
    server.worker.github.fetch_pr_info = AsyncMock(
        return_value={"head_sha": "abc", "base_sha": "def"}
    )

    seeded_result = ReviewResult(
        findings=[
            Finding(
                pass_name="security",
                severity="critical",
                file="a.py",
                line=3,
                title="T",
                description="D",
                suggestion="S",
            )
        ],
        usage=ReviewUsage(
            prompt_tokens=100, completion_tokens=200, per_pass={"security": (100, 200)}
        ),
    )

    async def fake_enqueue(job):
        # Simulate the worker finishing instantly so the first poll sees terminal.
        server.worker._record_job(job.job_id, "completed", result=seeded_result)

    server.worker.enqueue = fake_enqueue  # type: ignore[assignment]

    # TestClient is an httpx.Client subclass; client.py's sync post/get run the
    # real ASGI app end-to-end (auth, body parsing, registry, serialization).
    http_client = TestClient(server.app)
    result = review_via_server(
        server_url="http://testserver",
        server_key="test-api-key",
        owner="o",
        repo="r",
        pr_number=7,
        poll_budget=10.0,
        poll_interval=0.0,
        client=http_client,
    )

    assert isinstance(result, ReviewResult)
    assert result.findings[0].file == "a.py"
    # usage.per_pass survives the tuple → list (JSON) → tuple (model) round trip.
    assert result.usage.per_pass == {"security": (100, 200)}
    assert result.usage.prompt_tokens == 100
