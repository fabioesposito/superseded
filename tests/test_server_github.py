from __future__ import annotations

import hashlib
import hmac
from unittest.mock import patch

import httpx
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


def _transport(routes):
    def handler(request):
        for predicate, response in routes:
            if predicate(request):
                return response
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_compare_diff_ahead_returns_patch(app):
    routes = []

    def is_json(req):
        return req.url.path == "/repos/o/r/compare/a...b" and req.headers.get(
            "accept", ""
        ).startswith("application/vnd.github+json")

    def is_diff(req):
        return req.url.path == "/repos/o/r/compare/a...b" and "v3.diff" in req.headers.get(
            "accept", ""
        )

    routes.append((is_json, httpx.Response(200, json={"status": "ahead"})))
    routes.append((is_diff, httpx.Response(200, text="PATCH")))
    original = httpx.AsyncClient

    def _client(*a, **kw):
        kw["transport"] = _transport(routes)
        return original(*a, **kw)

    with patch("superseded.server.github.httpx.AsyncClient", side_effect=_client):
        patch_text, status = await app.compare_diff("tok", "o", "r", "a", "b")
    assert status == "ahead"
    assert patch_text == "PATCH"


@pytest.mark.asyncio
async def test_compare_diff_identical_returns_none(app):
    routes = [
        (
            lambda req: req.url.path == "/repos/o/r/compare/a...b",
            httpx.Response(200, json={"status": "identical"}),
        )
    ]
    original = httpx.AsyncClient

    def _client(*a, **kw):
        kw["transport"] = _transport(routes)
        return original(*a, **kw)

    with patch("superseded.server.github.httpx.AsyncClient", side_effect=_client):
        patch_text, status = await app.compare_diff("tok", "o", "r", "a", "b")
    assert status == "identical"
    assert patch_text is None


@pytest.mark.asyncio
async def test_compare_diff_behind_normalized_to_diverged(app):
    routes = [
        (
            lambda req: req.url.path == "/repos/o/r/compare/a...b",
            httpx.Response(200, json={"status": "behind"}),
        )
    ]
    original = httpx.AsyncClient

    def _client(*a, **kw):
        kw["transport"] = _transport(routes)
        return original(*a, **kw)

    with patch("superseded.server.github.httpx.AsyncClient", side_effect=_client):
        patch_text, status = await app.compare_diff("tok", "o", "r", "a", "b")
    assert status == "diverged"
    assert patch_text is None


@pytest.mark.asyncio
async def test_compare_diff_http_error_raises(app):
    routes = [
        (
            lambda req: req.url.path == "/repos/o/r/compare/a...b",
            httpx.Response(500),
        )
    ]
    original = httpx.AsyncClient

    def _client(*a, **kw):
        kw["transport"] = _transport(routes)
        return original(*a, **kw)

    with (
        patch("superseded.server.github.httpx.AsyncClient", side_effect=_client),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await app.compare_diff("tok", "o", "r", "a", "b")
