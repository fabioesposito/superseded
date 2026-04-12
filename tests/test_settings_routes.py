from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from superseded.main import create_app


@pytest.fixture
def tmp_repo():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        config_dir = repo / ".superseded"
        config_dir.mkdir(parents=True)
        yield str(repo)


async def _make_client(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), app


async def test_add_repo_valid(tmp_repo):
    client, _ = await _make_client(tmp_repo)
    async with client:
        response = await client.post(
            "/settings/repos",
            data={
                "name": "myrepo",
                "git_url": "https://github.com/user/repo.git",
                "path": "/tmp/test-repo",
                "branch": "main",
            },
        )
        assert response.status_code == 200
        assert "myrepo" in response.text


async def test_add_repo_invalid_git_url(tmp_repo):
    client, _ = await _make_client(tmp_repo)
    async with client:
        response = await client.post(
            "/settings/repos",
            data={
                "name": "badrepo",
                "git_url": "https://example.com; rm -rf /",
                "path": "/tmp/test-repo",
                "branch": "",
            },
        )
        assert response.status_code == 400
        assert "Invalid git URL" in response.text


async def test_add_repo_invalid_path(tmp_repo):
    client, _ = await _make_client(tmp_repo)
    async with client:
        response = await client.post(
            "/settings/repos",
            data={
                "name": "badrepo",
                "git_url": "",
                "path": "relative/path",
                "branch": "",
            },
        )
        assert response.status_code == 400
        assert "absolute" in response.text


async def test_add_repo_path_traversal(tmp_repo):
    client, _ = await _make_client(tmp_repo)
    async with client:
        response = await client.post(
            "/settings/repos",
            data={
                "name": "badrepo",
                "git_url": "",
                "path": "/foo/../../../etc",
                "branch": "",
            },
        )
        assert response.status_code == 400
        assert "traversal" in response.text


async def test_add_repo_empty_git_url_ok(tmp_repo):
    client, _ = await _make_client(tmp_repo)
    async with client:
        response = await client.post(
            "/settings/repos",
            data={
                "name": "nogit",
                "git_url": "",
                "path": "/tmp/test-repo",
                "branch": "",
            },
        )
        assert response.status_code == 200
        assert "nogit" in response.text


async def test_settings_page_loads(tmp_repo):
    client, _ = await _make_client(tmp_repo)
    async with client:
        response = await client.get("/settings")
        assert response.status_code == 200
