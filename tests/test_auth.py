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


async def test_no_auth_when_key_empty(tmp_repo):
    config = SupersededConfig(repo_path=tmp_repo, api_key="")
    app = create_app(config=config)
    await app.state.db.initialize()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
        assert resp.status_code == 200


async def test_auth_required_when_key_set(tmp_repo):
    config = SupersededConfig(repo_path=tmp_repo, api_key="secret123")
    app = create_app(config=config)
    await app.state.db.initialize()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
        assert resp.status_code == 401


async def test_auth_with_valid_header(tmp_repo):
    config = SupersededConfig(repo_path=tmp_repo, api_key="secret123")
    app = create_app(config=config)
    await app.state.db.initialize()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/", headers={"X-API-Key": "secret123"})
        assert resp.status_code == 200


async def test_health_endpoint_no_auth(tmp_repo):
    config = SupersededConfig(repo_path=tmp_repo, api_key="secret123")
    app = create_app(config=config)
    await app.state.db.initialize()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
