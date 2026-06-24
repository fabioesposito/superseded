from __future__ import annotations

import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from superseded.server.app import create_app
from superseded.server.config import ServerConfig
from superseded.server.github import GitHubApp
from superseded.server.repo_manager import RepoManager
from superseded.server.worker import ReviewWorker


@pytest.fixture
def app(tmp_path):
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
    application = create_app(config=config, github=github, worker=worker)
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


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
