from __future__ import annotations

import hashlib
import hmac

import pytest

from superseded.server.github import GitHubApp


@pytest.fixture
def app(tmp_path):
    key_file = tmp_path / "private.pem"
    key_file.write_text("fake-key")
    return GitHubApp(
        app_id=12345,
        private_key_path=key_file,
        webhook_secret="whsec_test_secret",
    )


def test_verify_webhook_valid(app):
    payload = b'{"action":"opened"}'
    signature = "sha256=" + hmac.new(b"whsec_test_secret", payload, hashlib.sha256).hexdigest()
    assert app.verify_webhook(payload, signature) is True


def test_verify_webhook_invalid(app):
    payload = b'{"action":"opened"}'
    signature = "sha256=" + "0" * 64
    assert app.verify_webhook(payload, signature) is False


def test_verify_webhook_timing_safe(app):
    payload = b'{"action":"opened"}'
    sig1 = "sha256=" + hmac.new(b"whsec_test_secret", payload, hashlib.sha256).hexdigest()
    sig2 = "sha256=" + "a" * 64
    app.verify_webhook(payload, sig1)
    app.verify_webhook(payload, sig2)


def test_verify_webhook_missing_prefix(app):
    payload = b'{"action":"opened"}'
    assert app.verify_webhook(payload, "invalid_format") is False


def test_verify_webhook_empty_signature(app):
    assert app.verify_webhook(b"payload", "") is False


def test_verify_webhook_rejects_empty_secret(tmp_path):
    key_file = tmp_path / "private.pem"
    key_file.write_text("fake-key")
    unconfigured = GitHubApp(
        app_id=0,
        private_key_path=key_file,
        webhook_secret="",
    )
    payload = b'{"action":"opened"}'
    signature = "sha256=" + hmac.new(b"", payload, hashlib.sha256).hexdigest()
    assert unconfigured.verify_webhook(payload, signature) is False


@pytest.mark.asyncio
async def test_fetch_repo_file_returns_contents(app, monkeypatch):
    captured: dict = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"content": "YWdlbnQ6IGNsYXVkZS1jb2RlCg==", "encoding": "base64"}

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None, params=None):
            captured["url"] = url
            captured["params"] = params
            return FakeResponse()

    monkeypatch.setattr("superseded.server.github.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr(
        "superseded.server.github.GitHubApp.fetch_default_branch",
        lambda self, token, owner, repo: _async_return("main"),
    )

    content = await app.fetch_repo_file("tok", "octocat", "hello-world", ".superseded.yaml")
    assert content == "agent: claude-code\n"
    assert "repos/octocat/hello-world/contents/.superseded.yaml" in captured["url"]
    assert captured["params"]["ref"] == "main"


async def _async_return(value):
    return value


@pytest.mark.asyncio
async def test_fetch_repo_file_returns_none_on_404(app, monkeypatch):
    import httpx

    class FakeResponse:
        status_code = 404

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError(
                "not found", request=httpx.Request("GET", "x"), response=httpx.Response(404)
            )

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None, params=None):
            return FakeResponse()

    monkeypatch.setattr("superseded.server.github.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr(
        "superseded.server.github.GitHubApp.fetch_default_branch",
        lambda self, token, owner, repo: _async_return("main"),
    )

    content = await app.fetch_repo_file("tok", "octocat", "hello-world", ".superseded.yaml")
    assert content is None


@pytest.mark.asyncio
async def test_update_check_run_uses_patch(app, monkeypatch):
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def patch(self, url, headers=None, json=None):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("superseded.server.github.httpx.AsyncClient", FakeClient)

    rid = await app.update_check_run(
        token="tok",
        owner="octocat",
        repo="hello-world",
        check_run_id=7,
        status="completed",
        conclusion="success",
        title="ok",
        summary="all good",
    )

    assert rid == 7
    assert captured["url"] == "https://api.github.com/repos/octocat/hello-world/check-runs/7"
    assert captured["json"]["status"] == "completed"
    assert captured["json"]["conclusion"] == "success"
    assert captured["json"]["output"] == {"title": "ok", "summary": "all good"}
