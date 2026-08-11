from __future__ import annotations

import hashlib
import hmac
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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
        health_token="test-health-token",
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
    response = client.get("/health", headers={"Authorization": "Bearer test-health-token"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "disk_usage" in data
    assert isinstance(data["disk_usage"], float | int)
    assert "uptime_seconds" in data
    assert isinstance(data["uptime_seconds"], (int, float))
    assert data["uptime_seconds"] >= 0


def test_health_without_token_hides_internals(tmp_path):
    """When no health_token is configured, /health returns only status."""
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
    worker = ReviewWorker(
        github=github, repo_manager=repo_manager, max_concurrent=1, provider=MagicMock()
    )
    from superseded.memory.store import MemoryStore

    store = MemoryStore(tmp_path / "memory.db")
    app = create_app(
        config=config,
        github=github,
        worker=worker,
        repo_manager=repo_manager,
        store=store,
    )
    response = TestClient(app).get("/health")
    data = response.json()
    assert data["status"] == "ok"
    assert "disk_usage" not in data
    assert "queue_depth" not in data


def test_health_with_token_requires_auth(tmp_path):
    """When health_token is set, /health requires Authorization header."""
    key_file = tmp_path / "key.pem"
    key_file.write_text("fake-key")
    config = ServerConfig(
        app_id=12345,
        webhook_secret="whsec_test",
        private_key_path=key_file,
        temp_dir=tmp_path / "repos",
        health_token="secret-health-token",
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
    from superseded.memory.store import MemoryStore

    store = MemoryStore(tmp_path / "memory.db")
    app = create_app(
        config=config,
        github=github,
        worker=worker,
        repo_manager=repo_manager,
        store=store,
    )
    client = TestClient(app)

    no_auth = client.get("/health")
    assert no_auth.status_code == 401

    wrong_auth = client.get("/health", headers={"Authorization": "Bearer wrong"})
    assert wrong_auth.status_code == 401

    good_auth = client.get("/health", headers={"Authorization": "Bearer secret-health-token"})
    assert good_auth.status_code == 200
    data = good_auth.json()
    assert "disk_usage" in data


def test_review_endpoint_returns_501_when_no_api_key_configured(client):
    response = client.post("/review")
    assert response.status_code == 501


def test_review_endpoint_returns_401_when_api_key_missing(tmp_path):
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
    store = __import__("superseded.memory.store", fromlist=["MemoryStore"]).MemoryStore(
        tmp_path / "mem.db"
    )
    app = create_app(
        config=config,
        github=github,
        worker=worker,
        repo_manager=repo_manager,
        store=store,
    )
    test_client = TestClient(app)
    response = test_client.post("/review")
    assert response.status_code == 401


def test_review_endpoint_returns_422_when_authenticated_but_body_invalid(tmp_path):
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
    store = __import__("superseded.memory.store", fromlist=["MemoryStore"]).MemoryStore(
        tmp_path / "mem2.db"
    )
    app = create_app(
        config=config,
        github=github,
        worker=worker,
        repo_manager=repo_manager,
        store=store,
    )
    test_client = TestClient(app)
    response = test_client.post(
        "/review",
        json={"owner": "test"},
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert response.status_code == 422


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
    import asyncio

    asyncio.run(server.store.init())
    asyncio.run(server.store.record_installation(12345, "octocat", ["hello-world"]))
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


def test_webhook_rate_limits_excessive_requests(server):
    """Sending >60 webhooks/min from the same IP should return 429."""
    import asyncio

    asyncio.run(server.store.init())
    asyncio.run(server.store.record_installation(12345, "octocat", ["hello-world"]))

    client = TestClient(server.app)
    payload = b'{"action":"opened","installation":{"id":12345},"pull_request":{"number":7,"head":{"sha":"abc"},"base":{"sha":"def"}},"repository":{"owner":{"login":"octocat"},"name":"hello-world"}}'
    secret = b"whsec_test"
    signature = "sha256=" + hmac.new(secret, payload, hashlib.sha256).hexdigest()
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": signature,
        "Content-Type": "application/json",
    }

    statuses = []
    for _ in range(65):
        response = client.post("/webhook", content=payload, headers=headers)
        statuses.append(response.status_code)
    assert 429 in statuses, f"Expected rate limiting (429), got statuses: {set(statuses)}"


def test_webhook_pr_event_skips_unauthorized_installation(server):
    """PR webhook for an installation not in the store must not enqueue."""
    import asyncio

    asyncio.run(server.store.init())

    client = TestClient(server.app)
    payload = b'{"action":"opened","installation":{"id":99999},"pull_request":{"number":7,"head":{"sha":"abc"},"base":{"sha":"def"}},"repository":{"owner":{"login":"octocat"},"name":"hello-world"}}'
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
    assert server.worker.queue.qsize() == 0


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


@pytest.fixture
def keyed_server(tmp_path):
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
    from superseded.memory.store import MemoryStore

    store = MemoryStore(tmp_path / "memory.db")
    application = create_app(
        config=config, github=github, worker=worker, repo_manager=repo_manager, store=store
    )
    return SimpleNamespace(
        app=application, worker=worker, github=github, config=config, store=store
    )


def test_review_pr_returns_501_when_no_api_key(client):
    response = client.post("/review/pr")
    assert response.status_code == 501


def test_review_pr_returns_401_when_api_key_missing(keyed_server):
    response = TestClient(keyed_server.app).post("/review/pr")
    assert response.status_code == 401


def test_review_pr_returns_422_when_body_invalid(keyed_server):
    response = TestClient(keyed_server.app).post(
        "/review/pr",
        json={"owner": "octocat"},
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert response.status_code == 422


def test_review_pr_returns_409_when_app_not_installed(keyed_server, monkeypatch):
    async def fake_resolve(owner, repo):
        return None

    monkeypatch.setattr(keyed_server.github, "resolve_installation", fake_resolve)
    response = TestClient(keyed_server.app).post(
        "/review/pr",
        json={"owner": "octocat", "repo": "hello-world", "pr_number": 7},
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert response.status_code == 409


def test_review_pr_enqueues_job(keyed_server, monkeypatch):
    import asyncio

    asyncio.run(keyed_server.store.init())
    asyncio.run(keyed_server.store.record_installation(12345, "octocat", ["hello-world"]))

    async def fake_resolve(owner, repo):
        return 12345

    async def fake_token(installation_id):
        return "ghp_fake"

    async def fake_pr_info(token, owner, repo, pr_number):
        return {"head_sha": "abc", "base_sha": "def", "title": "T"}

    monkeypatch.setattr(keyed_server.github, "resolve_installation", fake_resolve)
    monkeypatch.setattr(keyed_server.github, "get_installation_token", fake_token)
    monkeypatch.setattr(keyed_server.github, "fetch_pr_info", fake_pr_info)

    response = TestClient(keyed_server.app).post(
        "/review/pr",
        json={"owner": "octocat", "repo": "hello-world", "pr_number": 7, "passes": "security"},
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "enqueued"
    assert "job_id" in body
    assert keyed_server.worker.queue.qsize() == 1


def test_review_pr_returns_403_when_repo_not_authorized(keyed_server, monkeypatch):
    """A repo on an installed App but absent from authorized_repos is rejected."""
    import asyncio

    asyncio.run(keyed_server.store.init())
    asyncio.run(keyed_server.store.record_installation(12345, "octocat", ["other-repo"]))

    async def fake_resolve(owner, repo):
        return 12345

    monkeypatch.setattr(keyed_server.github, "resolve_installation", fake_resolve)

    response = TestClient(keyed_server.app).post(
        "/review/pr",
        json={"owner": "octocat", "repo": "hello-world", "pr_number": 7},
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert response.status_code == 403


def test_review_pr_returns_403_when_installation_unrecorded(keyed_server, monkeypatch):
    """App installed (resolve ok) but no installation event recorded -> 403."""
    import asyncio

    asyncio.run(keyed_server.store.init())

    async def fake_resolve(owner, repo):
        return 12345

    monkeypatch.setattr(keyed_server.github, "resolve_installation", fake_resolve)

    response = TestClient(keyed_server.app).post(
        "/review/pr",
        json={"owner": "octocat", "repo": "hello-world", "pr_number": 7},
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert response.status_code == 403


def test_review_pr_returns_502_when_pr_fetch_fails(keyed_server, monkeypatch):
    import asyncio

    asyncio.run(keyed_server.store.init())
    asyncio.run(keyed_server.store.record_installation(12345, "octocat", ["hello-world"]))

    async def fake_resolve(owner, repo):
        return 12345

    async def fake_token(installation_id):
        return "ghp_fake"

    async def fake_pr_info(token, owner, repo, pr_number):
        raise RuntimeError("boom")

    monkeypatch.setattr(keyed_server.github, "resolve_installation", fake_resolve)
    monkeypatch.setattr(keyed_server.github, "get_installation_token", fake_token)
    monkeypatch.setattr(keyed_server.github, "fetch_pr_info", fake_pr_info)

    response = TestClient(keyed_server.app).post(
        "/review/pr",
        json={"owner": "octocat", "repo": "hello-world", "pr_number": 7},
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert response.status_code == 502


def test_review_pr_post_false_propagates_to_job(keyed_server, monkeypatch):
    import asyncio

    asyncio.run(keyed_server.store.init())
    asyncio.run(keyed_server.store.record_installation(12345, "octocat", ["hello-world"]))

    async def fake_resolve(owner, repo):
        return 12345

    async def fake_token(installation_id):
        return "ghp_fake"

    async def fake_pr_info(token, owner, repo, pr_number):
        return {"head_sha": "abc", "base_sha": "def", "title": "T"}

    captured: dict = {}

    async def fake_enqueue(job):
        captured["job"] = job

    monkeypatch.setattr(keyed_server.github, "resolve_installation", fake_resolve)
    monkeypatch.setattr(keyed_server.github, "get_installation_token", fake_token)
    monkeypatch.setattr(keyed_server.github, "fetch_pr_info", fake_pr_info)
    monkeypatch.setattr(keyed_server.worker, "enqueue", fake_enqueue)

    response = TestClient(keyed_server.app).post(
        "/review/pr",
        json={"owner": "octocat", "repo": "hello-world", "pr_number": 7, "post": False},
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "enqueued"
    assert captured["job"].post is False


def test_review_pr_post_defaults_true_when_absent(keyed_server, monkeypatch):
    import asyncio

    asyncio.run(keyed_server.store.init())
    asyncio.run(keyed_server.store.record_installation(12345, "octocat", ["hello-world"]))

    async def fake_resolve(owner, repo):
        return 12345

    async def fake_token(installation_id):
        return "ghp_fake"

    async def fake_pr_info(token, owner, repo, pr_number):
        return {"head_sha": "abc", "base_sha": "def", "title": "T"}

    captured: dict = {}

    async def fake_enqueue(job):
        captured["job"] = job

    monkeypatch.setattr(keyed_server.github, "resolve_installation", fake_resolve)
    monkeypatch.setattr(keyed_server.github, "get_installation_token", fake_token)
    monkeypatch.setattr(keyed_server.github, "fetch_pr_info", fake_pr_info)
    monkeypatch.setattr(keyed_server.worker, "enqueue", fake_enqueue)

    response = TestClient(keyed_server.app).post(
        "/review/pr",
        json={"owner": "octocat", "repo": "hello-world", "pr_number": 7},
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "enqueued"
    assert captured["job"].post is True
