from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from superseded.config import SupersededConfig
from superseded.main import create_app


@pytest.fixture
def tmp_repo():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        issues_dir = repo / ".superseded" / "issues"
        issues_dir.mkdir(parents=True)
        yield str(tmp)


async def test_post_without_csrf_token_rejected(tmp_repo):
    config = SupersededConfig(repo_path=tmp_repo, api_key="")
    app = create_app(config=config)
    await app.state.db.initialize()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/issues/new",
            data={"title": "test", "body": "test"},
            follow_redirects=False,
        )
        assert resp.status_code == 403


async def test_post_with_csrf_token_accepted(tmp_repo):
    config = SupersededConfig(repo_path=tmp_repo, api_key="")
    app = create_app(config=config)
    await app.state.db.initialize()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # First GET to obtain CSRF token
        get_resp = await client.get("/issues/new")
        assert get_resp.status_code == 200
        # Extract token from cookie
        csrf_token = client.cookies.get("csrf_token", "")
        assert csrf_token  # Token should be set
        resp = await client.post(
            "/issues/new",
            data={"title": "test", "body": "test"},
            headers={"X-CSRF-Token": csrf_token},
            follow_redirects=False,
        )
        assert resp.status_code == 303


async def test_csrf_skipped_with_api_key(tmp_repo):
    config = SupersededConfig(repo_path=tmp_repo, api_key="secret123")
    app = create_app(config=config)
    await app.state.db.initialize()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/issues/new",
            data={"title": "test", "body": "test"},
            headers={"X-API-Key": "secret123"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
