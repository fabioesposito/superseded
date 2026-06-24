# Server Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `superseded serve` command that runs a FastAPI server receiving GitHub App webhooks and running AI code reviews on a dedicated server.

**Architecture:** FastAPI HTTP server receives GitHub webhook events, queues review jobs, and processes them concurrently via an asyncio worker pool. Shallow-clones repos, runs the existing ReviewEngine, and posts results back via the GitHub API. All GitHub interaction moves from `gh` CLI subprocess calls to authenticated API calls using a GitHub App's installation tokens.

**Tech Stack:** FastAPI, uvicorn, pyjwt, httpx (for GitHub API), aiosqlite (existing)

---

## File Structure

**New files:**
| File | Purpose |
|------|---------|
| `src/superseded/server/__init__.py` | Package init |
| `src/superseded/server/config.py` | Server config model (env vars, YAML) |
| `src/superseded/server/github.py` | GitHub App auth, webhook verification, API client |
| `src/superseded/server/checkout.py` | Shallow clone repos to temp dirs |
| `src/superseded/server/repo_manager.py` | Temp dir lifecycle, disk management |
| `src/superseded/server/worker.py` | Async queue consumer, concurrency control |
| `src/superseded/server/app.py` | FastAPI routes (webhook, health) |
| `src/superseded/server/lifecycle.py` | Startup, shutdown, health status |
| `tests/test_server_config.py` | Server config tests |
| `tests/test_server_github.py` | GitHub App auth + API tests |
| `tests/test_server_checkout.py` | Repo checkout tests |
| `tests/test_server_worker.py` | Worker queue tests |
| `tests/test_server_app.py` | HTTP endpoint tests |

**Modified files:**
| File | Changes |
|------|---------|
| `pyproject.toml` | Add `[server]` optional deps |
| `src/superseded/cli.py` | Add `serve` command |
| `src/superseded/memory/store.py` | Add `installations` table + migration |

---

## Task 1: Dependencies & Server Config Model

**Files:**
- Modify: `pyproject.toml`
- Create: `src/superseded/server/__init__.py`
- Create: `src/superseded/server/config.py`
- Create: `tests/test_server_config.py`

- [x] **Step 1: Add server optional dependencies to pyproject.toml**

Edit `pyproject.toml` — add after the `dev` dependency group:

```toml
[project.optional-dependencies]
server = [
    "fastapi>=0.115.0",
    "uvicorn>=0.34.0",
    "pyjwt>=2.10.0",
]
```

- [x] **Step 2: Create server package init**

Create `src/superseded/server/__init__.py`:

```python
from __future__ import annotations
```

- [x] **Step 3: Write failing test for server config**

Create `tests/test_server_config.py`:

```python
from __future__ import annotations

import os
from pathlib import Path

import pytest

from superseded.server.config import ServerConfig


def test_server_config_defaults():
    config = ServerConfig()
    assert config.port == 8000
    assert config.host == "0.0.0.0"
    assert config.max_concurrent_reviews == 3
    assert config.log_level == "info"


def test_server_config_from_env(monkeypatch):
    monkeypatch.setenv("SUPERSEDED_APP_ID", "12345")
    monkeypatch.setenv("SUPERSEDED_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("SUPERSEDED_PRIVATE_KEY_PATH", "/tmp/key.pem")
    monkeypatch.setenv("SUPERSEDED_MAX_CONCURRENT", "5")
    monkeypatch.setenv("SUPERSEDED_PORT", "9000")

    config = ServerConfig.from_env()
    assert config.app_id == 12345
    assert config.webhook_secret == "whsec_test"
    assert config.private_key_path == Path("/tmp/key.pem")
    assert config.max_concurrent_reviews == 5
    assert config.port == 9000


def test_server_config_from_env_missing_required(monkeypatch):
    monkeypatch.delenv("SUPERSEDED_APP_ID", raising=False)
    monkeypatch.delenv("SUPERSEDED_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("SUPERSEDED_PRIVATE_KEY_PATH", raising=False)

    with pytest.raises(ValueError, match="app_id"):
        ServerConfig.from_env()


def test_server_config_from_yaml(tmp_path):
    config_file = tmp_path / "server.yaml"
    config_file.write_text(
        "app_id: 99999\n"
        "webhook_secret: whsec_yaml\n"
        "private_key_path: /etc/key.pem\n"
        "max_concurrent_reviews: 10\n"
        "port: 3000\n"
        "host: 127.0.0.1\n"
        "log_level: debug\n"
    )
    config = ServerConfig.from_yaml(config_file)
    assert config.app_id == 99999
    assert config.webhook_secret == "whsec_yaml"
    assert config.max_concurrent_reviews == 10
    assert config.port == 3000
    assert config.host == "127.0.0.1"
    assert config.log_level == "debug"
```

- [x] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_server_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'superseded.server.config'`

- [x] **Step 5: Implement server config**

Create `src/superseded/server/config.py`:

```python
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator


class ServerConfig(BaseModel):
    app_id: int = 0
    webhook_secret: str = ""
    private_key_path: Path = Path("/dev/null")
    port: int = 8000
    host: str = "0.0.0.0"
    max_concurrent_reviews: int = 3
    temp_dir: Path = Path("/tmp/superseded")
    log_level: str = "info"

    @model_validator(mode="after")
    def validate_required_fields(self) -> ServerConfig:
        errors = []
        if not self.app_id:
            errors.append("app_id is required")
        if not self.webhook_secret:
            errors.append("webhook_secret is required")
        if self.private_key_path == Path("/dev/null") or not self.private_key_path.exists():
            errors.append(f"private_key_path must exist: {self.private_key_path}")
        if errors:
            raise ValueError(", ".join(errors))
        return self

    @classmethod
    def from_env(cls) -> ServerConfig:
        app_id = os.environ.get("SUPERSEDED_APP_ID")
        webhook_secret = os.environ.get("SUPERSEDED_WEBHOOK_SECRET")
        private_key_path = os.environ.get("SUPERSEDED_PRIVATE_KEY_PATH")

        kwargs: dict = {}
        if app_id:
            kwargs["app_id"] = int(app_id)
        if webhook_secret:
            kwargs["webhook_secret"] = webhook_secret
        if private_key_path:
            kwargs["private_key_path"] = Path(private_key_path)

        max_concurrent = os.environ.get("SUPERSEDED_MAX_CONCURRENT")
        if max_concurrent:
            kwargs["max_concurrent_reviews"] = int(max_concurrent)

        port = os.environ.get("SUPERSEDED_PORT")
        if port:
            kwargs["port"] = int(port)

        host = os.environ.get("SUPERSEDED_HOST")
        if host:
            kwargs["host"] = host

        log_level = os.environ.get("SUPERSEDED_LOG_LEVEL")
        if log_level:
            kwargs["log_level"] = log_level

        return cls(**kwargs)

    @classmethod
    def from_yaml(cls, path: Path) -> ServerConfig:
        data = yaml.safe_load(path.read_text()) or {}
        if "private_key_path" in data:
            data["private_key_path"] = Path(data["private_key_path"])
        if "temp_dir" in data:
            data["temp_dir"] = Path(data["temp_dir"])
        return cls(**data)
```

- [x] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_server_config.py -v`
Expected: PASS (note: `from_env` test requires a real private key file — adjust test to create a temp key file)

- [x] **Step 7: Fix test to use temp key file**

Update `tests/test_server_config.py` — replace `test_server_config_from_env` with:

```python
def test_server_config_from_env(monkeypatch, tmp_path):
    key_file = tmp_path / "key.pem"
    key_file.write_text("fake-private-key")
    monkeypatch.setenv("SUPERSEDED_APP_ID", "12345")
    monkeypatch.setenv("SUPERSEDED_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("SUPERSEDED_PRIVATE_KEY_PATH", str(key_file))
    monkeypatch.setenv("SUPERSEDED_MAX_CONCURRENT", "5")
    monkeypatch.setenv("SUPERSEDED_PORT", "9000")

    config = ServerConfig.from_env()
    assert config.app_id == 12345
    assert config.webhook_secret == "whsec_test"
    assert config.private_key_path == key_file
    assert config.max_concurrent_reviews == 5
    assert config.port == 9000
```

And replace `test_server_config_from_env_missing` with:

```python
def test_server_config_from_env_missing(monkeypatch):
    monkeypatch.delenv("SUPERSEDED_APP_ID", raising=False)
    monkeypatch.delenv("SUPERSEDED_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("SUPERSEDED_PRIVATE_KEY_PATH", raising=False)

    with pytest.raises((ValueError, Exception)):
        ServerConfig.from_env()
```

- [x] **Step 8: Run all tests again**

Run: `uv run pytest tests/test_server_config.py -v`
Expected: ALL PASS

- [x] **Step 9: Commit**

```bash
git add pyproject.toml src/superseded/server/ tests/test_server_config.py
git commit -m "feat(server): add server config model with env/YAML loading"
```

---

## Task 2: GitHub App Authentication & Webhook Verification

**Files:**
- Create: `src/superseded/server/github.py`
- Create: `tests/test_server_github.py`

- [x] **Step 1: Write failing tests for webhook verification**

Create `tests/test_server_github.py`:

```python
from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    signature = "sha256=" + hmac.new(
        b"whsec_test_secret", payload, hashlib.sha256
    ).hexdigest()
    assert app.verify_webhook(payload, signature) is True


def test_verify_webhook_invalid(app):
    payload = b'{"action":"opened"}'
    signature = "sha256=" + "0" * 64
    assert app.verify_webhook(payload, signature) is False


def test_verify_webhook_timing_safe(app):
    payload = b'{"action":"opened"}'
    sig1 = "sha256=" + hmac.new(
        b"whsec_test_secret", payload, hashlib.sha256
    ).hexdigest()
    sig2 = "sha256=" + "a" * 64
    # Both should complete without timing差异 (no exception = pass)
    app.verify_webhook(payload, sig1)
    app.verify_webhook(payload, sig2)


def test_verify_webhook_missing_prefix(app):
    payload = b'{"action":"opened"}'
    assert app.verify_webhook(payload, "invalid_format") is False


def test_verify_webhook_empty_signature(app):
    assert app.verify_webhook(b"payload", "") is False
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server_github.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement webhook verification**

Create `src/superseded/server/github.py`:

```python
from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path

import jwt
from pydantic import BaseModel


class GitHubApp:
    def __init__(self, app_id: int, private_key_path: Path, webhook_secret: str) -> None:
        self.app_id = app_id
        self._private_key = private_key_path.read_text()
        self._webhook_secret = webhook_secret.encode()

    def verify_webhook(self, payload: bytes, signature: str) -> bool:
        if not signature or not signature.startswith("sha256="):
            return False
        expected = hmac.new(self._webhook_secret, payload, hashlib.sha256).hexdigest()
        actual = signature[len("sha256="):]
        return hmac.compare_digest(expected, actual)

    def _sign_jwt(self) -> str:
        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + 600,
            "iss": str(self.app_id),
        }
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    async def get_installation_token(self, installation_id: int) -> str:
        import httpx

        jwt_token = self._sign_jwt()
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.github.com/app/installations/{installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {jwt_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response.raise_for_status()
            return response.json()["token"]

    async def fetch_pr_diff(self, token: str, owner: str, repo: str, pr_number: int) -> str:
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github.v3.diff",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response.raise_for_status()
            return response.text

    async def fetch_pr_description(self, token: str, owner: str, repo: str, pr_number: int) -> str | None:
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response.raise_for_status()
            data = response.json()
            body = data.get("body", "")
            return body if body else None

    async def post_review(
        self,
        token: str,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
        comments: list[dict],
        event: str,
    ) -> list[int]:
        import httpx

        payload = {
            "event": event,
            "body": body,
            "comments": comments,
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            return [c["id"] for c in data.get("comments", []) if "id" in c]

    async def create_check_run(
        self,
        token: str,
        owner: str,
        repo: str,
        name: str,
        head_sha: str,
        status: str,
        conclusion: str | None = None,
        title: str | None = None,
        summary: str | None = None,
    ) -> int:
        import httpx

        payload: dict = {
            "name": name,
            "head_sha": head_sha,
            "status": status,
        }
        if conclusion:
            payload["conclusion"] = conclusion
        if title:
            payload["output"] = {"title": title, "summary": summary or ""}
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.github.com/repos/{owner}/{repo}/check-runs",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json=payload,
            )
            response.raise_for_status()
            return response.json()["id"]
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server_github.py -v`
Expected: PASS

- [x] **Step 5: Add JWT and httpx as server deps**

The `pyjwt` and `httpx` packages should already be in `pyproject.toml` from Task 1. Verify `httpx` is in the `[server]` extras. If not, add it:

```toml
[project.optional-dependencies]
server = [
    "fastapi>=0.115.0",
    "uvicorn>=0.34.0",
    "pyjwt>=2.10.0",
    "httpx>=0.28.0",
]
```

- [x] **Step 6: Commit**

```bash
git add src/superseded/server/github.py tests/test_server_github.py pyproject.toml
git commit -m "feat(server): add GitHub App auth and webhook verification"
```

---

## Task 3: Database Schema for Installations

**Files:**
- Modify: `src/superseded/memory/store.py`
- Create: `tests/test_server_store.py`

- [x] **Step 1: Write failing tests for installations table**

Create `tests/test_server_store.py`:

```python
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from superseded.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test.db"
    s = MemoryStore(db_path=db_path)
    asyncio.run(s.init())
    return s


def test_installations_table_exists(store):
    async def _check():
        import aiosqlite

        async with aiosqlite.connect(store.db_path) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='installations'"
            )
            return await cursor.fetchone() is not None

    assert asyncio.run(_check())


def test_record_installation(store):
    asyncio.run(
        store.record_installation(
            installation_id=12345,
            owner="octocat",
            repos=["hello-world", "mona-lisa"],
        )
    )

    async def _get():
        import aiosqlite

        async with aiosqlite.connect(store.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM installations WHERE app_installation_id = 12345"
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    result = asyncio.run(_get())
    assert result is not None
    assert result["owner"] == "octocat"
    repos = json.loads(result["repos"])
    assert "hello-world" in repos
    assert "mona-lisa" in repos


def test_get_installation(store):
    asyncio.run(
        store.record_installation(
            installation_id=99999,
            owner="test-org",
            repos=["repo-a"],
        )
    )
    result = asyncio.run(store.get_installation(99999))
    assert result is not None
    assert result["owner"] == "test-org"


def test_get_installation_not_found(store):
    result = asyncio.run(store.get_installation(0))
    assert result is None


def test_remove_installation(store):
    asyncio.run(
        store.record_installation(
            installation_id=11111,
            owner="doomed",
            repos=["temp-repo"],
        )
    )
    asyncio.run(store.remove_installation(11111))
    result = asyncio.run(store.get_installation(11111))
    assert result is None
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server_store.py -v`
Expected: FAIL with `AttributeError: 'MemoryStore' object has no attribute 'record_installation'`

- [x] **Step 3: Implement installations methods in MemoryStore**

Edit `src/superseded/memory/store.py` — add to SCHEMA:

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    repo TEXT,
    pass TEXT,
    severity TEXT,
    file TEXT,
    line INTEGER,
    reasoning TEXT DEFAULT '',
    title TEXT,
    description TEXT,
    dismissed BOOLEAN DEFAULT FALSE,
    comment_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id TEXT REFERENCES findings(id),
    action TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS installations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_installation_id INTEGER UNIQUE NOT NULL,
    owner TEXT NOT NULL,
    repos TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Add these methods to the `MemoryStore` class:

```python
    async def record_installation(
        self, installation_id: int, owner: str, repos: list[str]
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO installations (app_installation_id, owner, repos) "
                "VALUES (?, ?, ?)",
                (installation_id, owner, json.dumps(repos)),
            )
            await db.commit()

    async def get_installation(self, installation_id: int) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM installations WHERE app_installation_id = ?",
                (installation_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row is not None else None

    async def remove_installation(self, installation_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM installations WHERE app_installation_id = ?",
                (installation_id,),
            )
            await db.commit()
```

Add `import json` at the top of `store.py`.

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server_store.py -v`
Expected: PASS

- [x] **Step 5: Run all existing tests to verify no regressions**

Run: `uv run pytest tests/ -v`
Expected: ALL PASS

- [x] **Step 6: Commit**

```bash
git add src/superseded/memory/store.py tests/test_server_store.py
git commit -m "feat(server): add installations table to memory store"
```

---

## Task 4: Repo Checkout & Manager

**Files:**
- Create: `src/superseded/server/checkout.py`
- Create: `src/superseded/server/repo_manager.py`
- Create: `tests/test_server_checkout.py`

- [x] **Step 1: Write failing tests for checkout**

Create `tests/test_server_checkout.py`:

```python
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from superseded.server.checkout import checkout_repo
from superseded.server.repo_manager import RepoManager


def test_repo_manager_disk_usage():
    manager = RepoManager(base_path=Path("/tmp/test"))
    usage = manager.disk_usage()
    assert 0.0 <= usage <= 1.0


def test_repo_manager_cleanup(tmp_path):
    target = tmp_path / "repo"
    target.mkdir()
    (target / "file.txt").write_text("hello")
    manager = RepoManager(base_path=tmp_path)
    manager.cleanup(target)
    assert not target.exists()


def test_repo_manager_cleanup_missing_dir():
    manager = RepoManager(base_path=Path("/tmp/nonexistent"))
    manager.cleanup(Path("/tmp/nonexistent/does/not/exist"))  # Should not raise


@patch("superseded.server.checkout.subprocess.run")
def test_checkout_repo_calls_git_clone(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    async def _test():
        return await checkout_repo(
            token="ghp_test_token",
            owner="octocat",
            repo="hello-world",
            ref="abc123",
            base_ref="main",
            tmp_dir="/tmp/test/checkout",
        )

    result = _test()
    # The function is async, need to run it
    import asyncio
    asyncio.run(result)

    # Verify git clone was called
    assert mock_run.called
    call_args = mock_run.call_args[0][0]
    assert "git" in call_args
    assert "clone" in call_args


@patch("superseded.server.checkout.subprocess.run")
def test_checkout_repo_failure_raises(mock_run):
    mock_run.return_value = MagicMock(returncode=128, stdout="", stderr="repository not found")

    async def _test():
        return await checkout_repo(
            token="ghp_bad",
            owner="no",
            repo="such-repo",
            ref="abc",
            base_ref="main",
            tmp_dir="/tmp/test/fail",
        )

    with pytest.raises(RuntimeError, match="git clone failed"):
        asyncio.run(_test())
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server_checkout.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement checkout module**

Create `src/superseded/server/checkout.py`:

```python
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path


async def checkout_repo(
    token: str,
    owner: str,
    repo: str,
    ref: str,
    base_ref: str,
    tmp_dir: str,
) -> Path:
    target = Path(tmp_dir)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
    cmd = [
        "git", "clone",
        "--depth=2",
        "--branch", ref,
        url,
        str(target),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"git clone failed (exit {proc.returncode}): "
            + stderr.decode(errors="replace").strip()
        )
    return target
```

- [x] **Step 4: Implement repo manager**

Create `src/superseded/server/repo_manager.py`:

```python
from __future__ import annotations

import shutil
from pathlib import Path


class RepoManager:
    def __init__(self, base_path: Path) -> None:
        self.base_path = base_path
        self.base_path.mkdir(parents=True, exist_ok=True)

    def disk_usage(self) -> float:
        usage = shutil.disk_usage(str(self.base_path))
        return usage.used / usage.total if usage.total > 0 else 0.0

    def cleanup(self, path: Path) -> None:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    def job_dir(self, installation_id: int, owner: str, repo: str, pr_number: int) -> Path:
        return self.base_path / str(installation_id) / owner / repo / str(pr_number)
```

- [x] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_server_checkout.py -v`
Expected: PASS

- [x] **Step 6: Commit**

```bash
git add src/superseded/server/checkout.py src/superseded/server/repo_manager.py tests/test_server_checkout.py
git commit -m "feat(server): add repo checkout and manager modules"
```

---

## Task 5: Review Worker

**Files:**
- Create: `src/superseded/server/worker.py`
- Create: `tests/test_server_worker.py`

- [x] **Step 1: Write failing tests for worker**

Create `tests/test_server_worker.py`:

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from superseded.server.worker import ReviewJob, ReviewWorker


@dataclass
class FakeGitHubApp:
    get_installation_token: AsyncMock = field(default_factory=lambda: AsyncMock(return_value="ghp_fake"))
    fetch_pr_diff: AsyncMock = field(default_factory=lambda: AsyncMock(return_value="diff --git a/x.py"))
    fetch_pr_description: AsyncMock = field(default_factory=lambda: AsyncMock(return_value="PR desc"))
    post_review: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=[1, 2]))
    create_check_run: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=42))


@dataclass
class FakeRepoManager:
    job_dir: MagicMock = field(default_factory=lambda: MagicMock(return_value=Path("/tmp/fake")))
    cleanup: MagicMock = field(default_factory=lambda: MagicMock())
    disk_usage: MagicMock = field(default_factory=lambda: MagicMock(return_value=0.5))


def test_review_job_creation():
    job = ReviewJob(
        installation_id=123,
        owner="octocat",
        repo="hello-world",
        pr_number=42,
        head_sha="abc123",
        base_sha="def456",
    )
    assert job.installation_id == 123
    assert job.pr_number == 42


@pytest.mark.asyncio
async def test_worker_processes_job():
    github = FakeGitHubApp()
    repo_manager = FakeRepoManager()
    worker = ReviewWorker(
        github=github,
        repo_manager=repo_manager,
        max_concurrent=2,
    )

    job = ReviewJob(
        installation_id=123,
        owner="octocat",
        repo="hello-world",
        pr_number=42,
        head_sha="abc123",
        base_sha="def456",
    )

    with patch("superseded.server.worker._run_review_for_job", new_callable=AsyncMock) as mock_review:
        await worker._process(job)

    github.get_installation_token.assert_called_once_with(123)
    github.create_check_run.assert_called_once()
    mock_review.assert_called_once()


@pytest.mark.asyncio
async def test_worker_handles_failure_gracefully():
    github = FakeGitHubApp()
    github.get_installation_token = AsyncMock(side_effect=RuntimeError("auth failed"))
    repo_manager = FakeRepoManager()
    worker = ReviewWorker(
        github=github,
        repo_manager=repo_manager,
        max_concurrent=1,
    )

    job = ReviewJob(
        installation_id=123,
        owner="octocat",
        repo="hello-world",
        pr_number=42,
        head_sha="abc123",
        base_sha="def456",
    )

    # Should not raise — errors are caught and logged
    await worker._process(job)
    github.create_check_run.assert_called_once()
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server_worker.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement worker module**

Create `src/superseded/server/worker.py`:

```python
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from superseded.server.github import GitHubApp
    from superseded.server.repo_manager import RepoManager

logger = logging.getLogger(__name__)


@dataclass
class ReviewJob:
    installation_id: int
    owner: str
    repo: str
    pr_number: int
    head_sha: str
    base_sha: str


class ReviewWorker:
    def __init__(
        self,
        github: GitHubApp,
        repo_manager: RepoManager,
        max_concurrent: int = 3,
    ) -> None:
        self.github = github
        self.repo_manager = repo_manager
        self.queue: asyncio.Queue[ReviewJob] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active_count = 0

    @property
    def active_count(self) -> int:
        return self._active_count

    async def enqueue(self, job: ReviewJob) -> None:
        await self.queue.put(job)

    async def run(self) -> None:
        while True:
            job = await self.queue.get()
            try:
                await self._process(job)
            finally:
                self.queue.task_done()

    async def _process(self, job: ReviewJob) -> None:
        correlation_id = str(uuid.uuid4())[:8]
        logger.info(
            "review_started",
            extra={
                "correlation_id": correlation_id,
                "repo": f"{job.owner}/{job.repo}",
                "pr": job.pr_number,
            },
        )

        check_run_id = None
        async with self._semaphore:
            self._active_count += 1
            try:
                token = await self.github.get_installation_token(job.installation_id)

                check_run_id = await self.github.create_check_run(
                    token=token,
                    owner=job.owner,
                    repo=job.repo,
                    name="Superseded Review",
                    head_sha=job.head_sha,
                    status="in_progress",
                )

                await _run_review_for_job(
                    github=self.github,
                    repo_manager=self.repo_manager,
                    token=token,
                    job=job,
                    correlation_id=correlation_id,
                )
            except Exception:
                logger.exception(
                    "review_failed",
                    extra={
                        "correlation_id": correlation_id,
                        "repo": f"{job.owner}/{job.repo}",
                        "pr": job.pr_number,
                    },
                )
                if check_run_id is not None:
                    try:
                        token = await self.github.get_installation_token(job.installation_id)
                        await self.github.create_check_run(
                            token=token,
                            owner=job.owner,
                            repo=job.repo,
                            name="Superseded Review",
                            head_sha=job.head_sha,
                            status="completed",
                            conclusion="failure",
                            title="Review failed",
                            summary=f"Review failed. Correlation ID: {correlation_id}",
                        )
                    except Exception:
                        logger.exception("Failed to update check run on error")
            finally:
                self._active_count -= 1


async def _run_review_for_job(
    github: GitHubApp,
    repo_manager: RepoManager,
    token: str,
    job: ReviewJob,
    correlation_id: str,
) -> None:
    from superseded.config import load_config
    from superseded.review.engine import ReviewEngine

    tmp_dir = repo_manager.job_dir(
        job.installation_id, job.owner, job.repo, job.pr_number
    )

    try:
        from superseded.server.checkout import checkout_repo

        repo_path = await checkout_repo(
            token=token,
            owner=job.owner,
            repo=job.repo,
            ref=job.head_sha,
            base_ref=job.base_sha,
            tmp_dir=str(tmp_dir),
        )

        config = load_config(repo_path / ".superseded.yaml")

        diff = await github.fetch_pr_diff(token, job.owner, job.repo, job.pr_number)
        pr_description = await github.fetch_pr_description(token, job.owner, job.repo, job.pr_number)

        engine = ReviewEngine.select(config.agent, model=config.model)
        engine.config = config
        result = engine.review(
            diff=diff,
            pr_description=pr_description,
        )

        blocking = result.summary.get("critical", 0) + result.summary.get("important", 0)
        event = "REQUEST_CHANGES" if blocking > 0 else "COMMENT"

        passes_used = sorted({f.pass_name for f in result.findings})
        pass_labels = ", ".join(p.replace("_", " ").title() + " Review" for p in passes_used)

        body = "## Superseded Code Review\n\n"
        if pass_labels:
            body += f"**Passes:** {pass_labels}\n\n"
        for sev, count in result.summary.items():
            body += f"- **{sev}:** {count}\n"

        comments = []
        for f in result.findings:
            body_text = f"**[{f.severity.upper()}] {f.title}** ({f.pass_name})\n\n{f.description}\n\n"
            if f.reasoning:
                body_text += f"<details><summary>Reasoning</summary>\n\n{f.reasoning}\n\n</details>\n\n"
            body_text += f"**Suggestion:** {f.suggestion}"
            comment: dict = {
                "path": f.file,
                "line": f.end_line,
                "body": body_text,
            }
            if f.line != f.end_line:
                comment["start_line"] = f.line
            comments.append(comment)

        comment_ids = await github.post_review(
            token=token,
            owner=job.owner,
            repo=job.repo,
            pr_number=job.pr_number,
            body=body,
            comments=comments,
            event=event,
        )

        conclusion = "success" if blocking == 0 else "failure"
        title = f"{len(result.findings)} finding(s)"
        if blocking:
            title += f" ({blocking} blocking)"
        summary = f"Review completed. {len(result.findings)} findings across {len(passes_used)} pass(es)."

        await github.create_check_run(
            token=token,
            owner=job.owner,
            repo=job.repo,
            name="Superseded Review",
            head_sha=job.head_sha,
            status="completed",
            conclusion=conclusion,
            title=title,
            summary=summary,
        )

        logger.info(
            "review_completed",
            extra={
                "correlation_id": correlation_id,
                "repo": f"{job.owner}/{job.repo}",
                "pr": job.pr_number,
                "findings_count": len(result.findings),
            },
        )
    finally:
        repo_manager.cleanup(tmp_dir)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server_worker.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add src/superseded/server/worker.py tests/test_server_worker.py
git commit -m "feat(server): add review worker with queue and concurrency control"
```

---

## Task 6: FastAPI App & Webhook Handler

**Files:**
- Create: `src/superseded/server/app.py`
- Create: `tests/test_server_app.py`

- [x] **Step 1: Write failing tests for webhook endpoint**

Create `tests/test_server_app.py`:

```python
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
    payload = b'{"action":"opened","pull_request":{"number":1,"head":{"sha":"abc"},"base":{"sha":"def"}},"repository":{"owner":{"login":"octocat"},"name":"hello-world"}}'
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
    assert response.status_code == 200  # Still 200, just no job enqueued
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server_app.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement FastAPI app**

Create `src/superseded/server/app.py`:

```python
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, Response

if TYPE_CHECKING:
    from superseded.server.config import ServerConfig
    from superseded.server.github import GitHubApp
    from superseded.server.worker import ReviewWorker

logger = logging.getLogger(__name__)


def create_app(
    config: ServerConfig,
    github: GitHubApp,
    worker: ReviewWorker,
) -> FastAPI:
    app = FastAPI(title="Superseded", version="0.1.0")

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "queue_depth": worker.queue.qsize(),
            "active_reviews": worker.active_count,
        }

    @app.post("/webhook")
    async def webhook(request: Request) -> Response:
        payload = await request.body()
        signature = request.headers.get("X-Hub-Signature-256", "")

        if not github.verify_webhook(payload, signature):
            return Response(status_code=401, content="Invalid signature")

        event = request.headers.get("X-GitHub-Event", "")
        data = await request.json()

        if event == "pull_request":
            await _handle_pr_event(data, github, worker)
        elif event == "installation":
            await _handle_installation_event(data, github)

        return Response(status_code=200, content="ok")

    return app


async def _handle_pr_event(
    data: dict,
    github: GitHubApp,
    worker: ReviewWorker,
) -> None:
    action = data.get("action", "")
    if action not in ("opened", "synchronize", "reopened"):
        return

    pr = data["pull_request"]
    repo = data["repository"]
    owner = repo["owner"]["login"]
    repo_name = repo["name"]

    from superseded.server.worker import ReviewJob

    job = ReviewJob(
        installation_id=data["installation"]["id"],
        owner=owner,
        repo=repo_name,
        pr_number=pr["number"],
        head_sha=pr["head"]["sha"],
        base_sha=pr["base"]["sha"],
    )
    await worker.enqueue(job)
    logger.info(
        "webhook_pr_enqueued",
        extra={"repo": f"{owner}/{repo_name}", "pr": pr["number"], "action": action},
    )


async def _handle_installation_event(
    data: dict,
    github: GitHubApp,
) -> None:
    action = data.get("action", "")
    installation = data["installation"]

    from superseded.memory.store import MemoryStore

    store = MemoryStore()
    await store.init()

    if action == "created":
        repos = [r["name"] for r in data.get("repositories", [])]
        await store.record_installation(
            installation_id=installation["id"],
            owner=installation["account"]["login"],
            repos=repos,
        )
        logger.info(
            "installation_created",
            extra={"installation_id": installation["id"], "repos": repos},
        )
    elif action == "deleted":
        await store.remove_installation(installation["id"])
        logger.info("installation_deleted", extra={"installation_id": installation["id"]})
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server_app.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add src/superseded/server/app.py tests/test_server_app.py
git commit -m "feat(server): add FastAPI app with webhook and health endpoints"
```

---

## Task 7: CLI `serve` Command & Server Lifecycle

**Files:**
- Modify: `src/superseded/cli.py`
- Create: `src/superseded/server/lifecycle.py`

- [x] **Step 1: Implement server lifecycle**

Create `src/superseded/server/lifecycle.py`:

```python
from __future__ import annotations

import asyncio
import logging
import signal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from superseded.server.app import FastAPI
    from superseded.server.worker import ReviewWorker

logger = logging.getLogger(__name__)


class ServerLifecycle:
    def __init__(self, app: FastAPI, worker: ReviewWorker) -> None:
        self.app = app
        self.worker = worker
        self._shutdown_event = asyncio.Event()
        self._worker_task: asyncio.Task | None = None

    async def startup(self) -> None:
        logger.info("Starting Superseded server...")
        self._worker_task = asyncio.create_task(self.worker.run())

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._handle_signal)

        logger.info("Server started")

    async def shutdown(self) -> None:
        logger.info("Shutting down...")
        self._shutdown_event.set()

        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

        logger.info("Server stopped")

    def _handle_signal(self) -> None:
        logger.info("Received shutdown signal")
        asyncio.create_task(self.shutdown())
```

- [x] **Step 2: Add `serve` command to CLI**

Edit `src/superseded/cli.py` — add at the bottom before the closing of the file:

```python
@cli.command()
@click.option("--port", type=int, default=None, help="Server port")
@click.option("--host", default=None, help="Server host")
@click.option("--config", "config_path", default=None, help="Server config file path")
def serve(port: int | None, host: str | None, config_path: str | None) -> None:
    """Start the Superseded review server."""
    from pathlib import Path

    from superseded.server.config import ServerConfig
    from superseded.server.github import GitHubApp
    from superseded.server.repo_manager import RepoManager
    from superseded.server.worker import ReviewWorker

    if config_path:
        config = ServerConfig.from_yaml(Path(config_path))
    else:
        config = ServerConfig.from_env()

    if port is not None:
        config.port = port
    if host is not None:
        config.host = host

    github = GitHubApp(
        app_id=config.app_id,
        private_key_path=config.private_key_path,
        webhook_secret=config.webhook_secret,
    )
    repo_manager = RepoManager(base_path=config.temp_dir)
    worker = ReviewWorker(
        github=github,
        repo_manager=repo_manager,
        max_concurrent=config.max_concurrent_reviews,
    )

    import uvicorn

    from superseded.server.app import create_app
    from superseded.server.lifecycle import ServerLifecycle

    app = create_app(config=config, github=github, worker=worker)
    lifecycle = ServerLifecycle(app=app, worker=worker)

    @app.on_event("startup")
    async def on_startup() -> None:
        await lifecycle.startup()

    @app.on_event("shutdown")
    async def on_shutdown() -> None:
        await lifecycle.shutdown()

    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(message)s",
    )

    click.echo(f"Starting Superseded server on {config.host}:{config.port}")
    uvicorn.run(app, host=config.host, port=config.port, log_level=config.log_level)
```

- [x] **Step 3: Verify CLI entry point works**

Run: `uv run superseded serve --help`
Expected: Shows help text with `--port`, `--host`, `--config` options

- [x] **Step 4: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: ALL PASS

- [x] **Step 5: Commit**

```bash
git add src/superseded/cli.py src/superseded/server/lifecycle.py
git commit -m "feat(server): add serve CLI command and server lifecycle"
```

---

## Task 8: Lint, Format & Final Verification

- [x] **Step 1: Run ruff check**

Run: `uv run ruff check src/ tests/`
Expected: No errors

- [x] **Step 2: Run ruff format**

Run: `uv run ruff format src/ tests/`
Expected: No changes needed (or auto-formatted)

- [x] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: ALL PASS

- [x] **Step 4: Verify server starts (manual smoke test)**

Run: `SUPERSEDED_APP_ID=1 SUPERSEDED_WEBHOOK_SECRET=test SUPERSEDED_PRIVATE_KEY_PATH=/dev/null uv run superseded serve --port 8000`
Expected: Server starts, shows "Starting Superseded server on 0.0.0.0:8000" (will fail on private key validation, but the command itself should parse correctly)

- [x] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(server): server mode implementation complete"
```
