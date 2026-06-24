from __future__ import annotations

import hashlib
import hmac
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from superseded.server.app import create_app
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
    )
    github = GitHubApp(
        app_id=config.app_id,
        private_key_path=config.private_key_path,
        webhook_secret=config.webhook_secret,
    )
    repo_manager = RepoManager(base_path=config.temp_dir)
    worker = ReviewWorker(github=github, repo_manager=repo_manager, max_concurrent=1)
    from superseded.memory.store import MemoryStore

    store = MemoryStore(tmp_path / "memory.db")
    application = create_app(
        config=config,
        github=github,
        worker=worker,
        repo_manager=repo_manager,
        store=store,
    )
    return SimpleNamespace(
        app=application,
        store=store,
        worker=worker,
        repo_manager=repo_manager,
        github=github,
    )


@pytest.fixture
def app(server):
    return server.app


@pytest.fixture
def client(server):
    return TestClient(server.app)


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "disk_usage" in data
    assert isinstance(data["disk_usage"], float | int)
    assert "uptime_seconds" in data
    assert isinstance(data["uptime_seconds"], (int, float))
    assert data["uptime_seconds"] >= 0


def test_review_endpoint_not_implemented(client):
    response = client.post("/review")
    assert response.status_code == 501


def test_webhook_accepts_push_event(client):
    payload = b'{"ref":"refs/heads/main"}'
    secret = b"whsec_test"
    signature = "sha256=" + hmac.new(secret, payload, hashlib.sha256).hexdigest()
    response = client.post(
        "/webhook",
        content=payload,
        headers={
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200


def test_webhook_rejects_bad_signature(client):
    payload = b'{"action":"opened"}'
    response = client.post(
        "/webhook",
        content=payload,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": "sha256=" + "0" * 64,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401


def test_webhook_accepts_valid_signature(client):
    payload = b'{"action":"opened","installation":{"id":12345},"pull_request":{"number":1,"head":{"sha":"abc"},"base":{"sha":"def"}},"repository":{"owner":{"login":"octocat"},"name":"hello-world"}}'
    secret = b"whsec_test"
    signature = "sha256=" + hmac.new(secret, payload, hashlib.sha256).hexdigest()

    response = client.post(
        "/webhook",
        content=payload,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200


def test_webhook_returns_200_for_installation_event(client):
    payload = b'{"action":"created","installation":{"id":99999,"account":{"login":"octocat"}},"repositories":[{"name":"repo1"}]}'
    secret = b"whsec_test"
    signature = "sha256=" + hmac.new(secret, payload, hashlib.sha256).hexdigest()

    response = client.post(
        "/webhook",
        content=payload,
        headers={
            "X-GitHub-Event": "installation",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200


def test_webhook_ignores_closed_pr(client):
    payload = b'{"action":"closed","pull_request":{"number":1,"head":{"sha":"abc"},"base":{"sha":"def"}},"repository":{"owner":{"login":"octocat"},"name":"hello-world"}}'
    secret = b"whsec_test"
    signature = "sha256=" + hmac.new(secret, payload, hashlib.sha256).hexdigest()

    response = client.post(
        "/webhook",
        content=payload,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200


def test_webhook_pr_event_enqueues_review(server):
    client = TestClient(server.app)
    payload = b'{"action":"opened","installation":{"id":12345},"pull_request":{"number":7,"head":{"sha":"abc"},"base":{"sha":"def"}},"repository":{"owner":{"login":"octocat"},"name":"hello-world"}}'
    secret = b"whsec_test"
    signature = "sha256=" + hmac.new(secret, payload, hashlib.sha256).hexdigest()

    response = client.post(
        "/webhook",
        content=payload,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200
    assert server.worker.queue.qsize() == 1


@pytest.mark.asyncio
async def test_handle_installation_event_uses_injected_store():
    from superseded.server.app import _handle_installation_event

    store = SimpleNamespace(
        init=AsyncMock(),
        record_installation=AsyncMock(),
        remove_installation=AsyncMock(),
    )
    data = {
        "action": "created",
        "installation": {"id": 99999, "account": {"login": "octocat"}},
        "repositories": [{"name": "repo1"}, {"name": "repo2"}],
    }
    await _handle_installation_event(data, store)
    store.init.assert_awaited_once()
    store.record_installation.assert_awaited_once()
    call = store.record_installation.await_args
    assert call.kwargs["installation_id"] == 99999
    assert call.kwargs["owner"] == "octocat"
    assert call.kwargs["repos"] == ["repo1", "repo2"]
