# Server Endpoint & GitHub Action Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the GitHub Action a thin HTTP client that POSTs to a running review server, and make the server run each review's agents inside an `sbx` Docker Sandbox (the executor foundation from Plan 1), posting results back via its GitHub App.

**Architecture:** Add `POST /review/pr` (owner/repo/pr_number) to the server, which resolves the GitHub App installation, fetches PR info, and enqueues a `ReviewJob`. The worker builds a `SandboxExecutor` (from Plan 1's `review/executor.py`) when sandbox mode is enabled and passes it to `engine.review`; if `sbx` is unavailable the job fails loudly. `action.yml` becomes a `composite` action that `curl`s the server (env-over-input URL/key) and exits; the server owns posting.

**Tech Stack:** Python 3.14+, FastAPI/uvicorn, pydantic v2, httpx, click, pytest (`asyncio_mode = "auto"`), ruff (`E,W,F,I,N,UP,B,SIM,TCH,RUF`). Commands via `uv run …`. All external binaries/network mocked in tests.

**Depends on:** Plan 1 (`feat/sandbox-executor` branch) — `review/executor.py` (`SubprocessExecutor`, `SandboxExecutor`, `make_sandbox_executor`, `SBX_AGENT_MAP`), engine `executor=` param, `Config.sandbox`.

**Spec:** `docs/superseded/specs/2026-07-01-sandbox-executor-and-server-action-design.md`

---

## File structure

- **Modify** `src/superseded/server/config.py` — add sandbox fields to `ServerConfig` + `from_env()`.
- **Modify** `src/superseded/server/github.py` — add `resolve_installation(owner, repo)`.
- **Modify** `src/superseded/server/app.py` — add `POST /review/pr` endpoint.
- **Modify** `src/superseded/server/worker.py` — `SandboxSettings` dataclass; `ReviewWorker` + `_run_review_for_job` build/pass a `SandboxExecutor`; `ReviewJob` gains optional `passes`.
- **Modify** `src/superseded/cli.py` — `serve` builds `SandboxSettings` from `ServerConfig` and passes it to the worker.
- **Rewrite** `action.yml` — `composite` action that POSTs to the server.
- **Remove** `docker/entrypoint.sh`; **modify** `docker/Dockerfile` (drop the `entrypoint.sh` COPY from the `cli` stage).
- **Modify** `compose.yml` — add `SUPERSEDED_SANDBOX` env.
- **Modify** `README.md` — document the breaking Action change (server URL/key + App install).
- **Tests:** `tests/test_server_config.py`, `tests/test_server_github.py`, `tests/test_server_app.py`, `tests/test_server_worker.py`, `tests/test_action.py` (new).

---

## Task 1: `ServerConfig` sandbox fields

**Files:**
- Modify: `src/superseded/server/config.py`
- Test: `tests/test_server_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server_config.py`:

```python
def test_server_config_sandbox_defaults():
    config = ServerConfig()
    assert config.sandbox_enabled is True
    assert config.sbx_binary == "sbx"
    assert config.sandbox_timeout == 600
    assert config.sandbox_keep_on_error is False
    assert config.sandbox_io_mode == "exec"


def test_server_config_sandbox_from_env(monkeypatch, tmp_path):
    key_file = tmp_path / "key.pem"
    key_file.write_text("fake-private-key")
    monkeypatch.setenv("SUPERSEDED_APP_ID", "12345")
    monkeypatch.setenv("SUPERSEDED_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("SUPERSEDED_PRIVATE_KEY_PATH", str(key_file))
    monkeypatch.setenv("SUPERSEDED_SANDBOX", "0")
    monkeypatch.setenv("SUPERSEDED_SANDBOX_TIMEOUT", "900")
    monkeypatch.setenv("SUPERSEDED_SANDBOX_IO_MODE", "cp")

    config = ServerConfig.from_env()
    assert config.sandbox_enabled is False
    assert config.sandbox_timeout == 900
    assert config.sandbox_io_mode == "cp"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server_config.py -v`
Expected: FAIL (sandbox fields absent).

- [ ] **Step 3: Implement**

In `src/superseded/server/config.py`, add these fields to `ServerConfig` (after `behind_proxy: bool = False`):

```python
    sandbox_enabled: bool = True
    sbx_binary: str = "sbx"
    sandbox_timeout: int = 600
    sandbox_keep_on_error: bool = False
    sandbox_io_mode: str = "exec"
```

In `ServerConfig.from_env()`, before the final `return cls(**kwargs)`, add env parsing mirroring the existing patterns:

```python
    sandbox = os.environ.get("SUPERSEDED_SANDBOX")
    if sandbox:
        kwargs["sandbox_enabled"] = sandbox.strip().lower() in ("1", "true", "yes", "on")

    sbx_binary = os.environ.get("SUPERSEDED_SBX_BINARY")
    if sbx_binary:
        kwargs["sbx_binary"] = sbx_binary

    sandbox_timeout = os.environ.get("SUPERSEDED_SANDBOX_TIMEOUT")
    if sandbox_timeout:
        kwargs["sandbox_timeout"] = int(sandbox_timeout)

    sandbox_keep = os.environ.get("SUPERSEDED_SANDBOX_KEEP_ON_ERROR")
    if sandbox_keep:
        kwargs["sandbox_keep_on_error"] = sandbox_keep.strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    sandbox_io = os.environ.get("SUPERSEDED_SANDBOX_IO_MODE")
    if sandbox_io:
        kwargs["sandbox_io_mode"] = sandbox_io
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server_config.py -v`
Expected: PASS (all).

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check src/superseded/server/config.py tests/test_server_config.py && uv run ruff format src/superseded/server/config.py tests/test_server_config.py`

- [ ] **Step 6: Commit**

```bash
git add src/superseded/server/config.py tests/test_server_config.py
git commit -m "feat: add sandbox fields to ServerConfig"
```

---

## Task 2: `GitHubApp.resolve_installation`

**Files:**
- Modify: `src/superseded/server/github.py`
- Test: `tests/test_server_github.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server_github.py` (read it first to match its existing fixtures/helpers; it constructs `GitHubApp` and mocks `httpx.AsyncClient` — mirror that style):

```python
@pytest.mark.asyncio
async def test_resolve_installation_returns_id(monkeypatch):
    github = _make_github()
    captured = {}

    class FakeResp:
        def __init__(self, status, payload):
            self.status_code = status
            self._payload = payload

        def raise_for_status(self):
            if self.status_code >= 400:
                import httpx

                raise httpx.HTTPStatusError("boom", request=None, response=self)

        def json(self):
            return self._payload

    class FakeClient:
        def __aenter__(self):
            return self

        def __exit__(self, *a):
            return None

        async def get(self, url, headers=None):
            captured["url"] = url
            return FakeResp(200, {"id": 777})

    monkeypatch.setattr("superseded.server.github.httpx.AsyncClient", lambda: FakeClient())
    assert await github.resolve_installation("octocat", "hello-world") == 777
    assert "repos/octocat/hello-world/installation" in captured["url"]


@pytest.mark.asyncio
async def test_resolve_installation_returns_none_when_not_installed(monkeypatch):
    github = _make_github()

    class FakeResp:
        status_code = 404

        def raise_for_status(self):
            pass

        def json(self):
            return {}

    class FakeClient:
        def __aenter__(self):
            return self

        def __exit__(self, *a):
            return None

        async def get(self, url, headers=None):
            return FakeResp()

    monkeypatch.setattr("superseded.server.github.httpx.AsyncClient", lambda: FakeClient())
    assert await github.resolve_installation("octocat", "hello-world") is None
```

NOTE: `_make_github()` is a helper you will ADD at the top of `tests/test_server_github.py` (if an equivalent doesn't already exist) that builds a real `GitHubApp` with a temp key file — read the file first; if there is already a fixture/helper building a `GitHubApp`, reuse it by that name instead of adding `_make_github`. The goal: construct a `GitHubApp` instance whose `_sign_jwt()` returns a string (a fake key works because `_sign_jwt` only calls `jwt.encode`, which accepts any string for RS256 at runtime in tests). If `jwt.encode` rejects the fake key, write a minimal valid-looking PEM. Confirm by running the test.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server_github.py -v`
Expected: FAIL (`resolve_installation` not defined).

- [ ] **Step 3: Implement**

Add this method to `GitHubApp` in `src/superseded/server/github.py` (place it after `_api_headers` / near `get_installation_token`):

```python
    async def resolve_installation(self, owner: str, repo: str) -> int | None:
        """Resolve the GitHub App installation id for a repository.

        Returns the installation id, or ``None`` if the app is not installed on
        the repository (HTTP 404). Uses the app JWT (not an installation token).
        Raises ``httpx.HTTPStatusError`` on other non-2xx responses.
        """
        jwt_token = self._sign_jwt()
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/installation",
                headers=self._api_headers(jwt_token),
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()["id"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server_github.py -v`
Expected: PASS (all, including the two new tests).

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check src/superseded/server/github.py tests/test_server_github.py && uv run ruff format src/superseded/server/github.py tests/test_server_github.py`

- [ ] **Step 6: Commit**

```bash
git add src/superseded/server/github.py tests/test_server_github.py
git commit -m "feat: add GitHubApp.resolve_installation"
```

---

## Task 3: `POST /review/pr` endpoint

**Files:**
- Modify: `src/superseded/server/app.py`
- Test: `tests/test_server_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server_app.py`. (The existing `server` fixture builds a config WITHOUT `api_key`; add a new helper that builds a server with `api_key="test-api-key"`. Read the file first — the `server` fixture pattern at the top is the template.)

```python
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
    worker = ReviewWorker(github=github, repo_manager=repo_manager, max_concurrent=1)
    from superseded.memory.store import MemoryStore

    store = MemoryStore(tmp_path / "memory.db")
    application = create_app(
        config=config, github=github, worker=worker, repo_manager=repo_manager, store=store
    )
    return SimpleNamespace(app=application, worker=worker, github=github, config=config)


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


def test_review_pr_returns_502_when_pr_fetch_fails(keyed_server, monkeypatch):
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server_app.py -v`
Expected: FAIL (no `/review/pr` route).

- [ ] **Step 3: Implement**

In `src/superseded/server/app.py`, add a new route inside `create_app(...)`, placed right after the existing `@app.post("/review")` `manual_review` function (before `@app.post("/webhook")`):

```python
    @app.post("/review/pr")
    async def review_pr(request: Request) -> Response:
        if not config.api_key:
            return Response(status_code=501, content="API key not configured on this server.")
        auth = request.headers.get("Authorization", "")
        expected = f"Bearer {config.api_key}"
        if not hmac.compare_digest(auth, expected):
            raise HTTPException(status_code=401, detail="Unauthorized")

        from superseded.server.worker import ReviewJob

        body = await request.json()
        try:
            owner = body["owner"]
            repo = body["repo"]
            pr_number = int(body["pr_number"])
        except (KeyError, ValueError) as err:
            raise HTTPException(status_code=422, detail=f"Missing or invalid field: {err}") from err

        passes_raw = body.get("passes")
        passes_list: list[str] | None = None
        if isinstance(passes_raw, str) and passes_raw.strip():
            passes_list = [p.strip() for p in passes_raw.split(",") if p.strip()]

        installation_id = await github.resolve_installation(owner, repo)
        if installation_id is None:
            raise HTTPException(status_code=409, detail="GitHub App is not installed on this repository.")

        try:
            token = await github.get_installation_token(installation_id)
            pr_info = await github.fetch_pr_info(
                token=token, owner=owner, repo=repo, pr_number=pr_number
            )
        except Exception as err:
            raise HTTPException(status_code=502, detail=f"Failed to fetch PR info: {err}") from err

        job = ReviewJob(
            installation_id=installation_id,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            head_sha=pr_info["head_sha"],
            base_sha=pr_info["base_sha"],
            passes=passes_list,
        )
        await worker.enqueue(job)
        logger.info(
            "review_pr_enqueued",
            extra={"repo": f"{owner}/{repo}", "pr": pr_number, "job_id": job.job_id},
        )
        return {"status": "enqueued", "job_id": job.job_id}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server_app.py -v`
Expected: PASS (all, including the new `/review/pr` tests).

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check src/superseded/server/app.py tests/test_server_app.py && uv run ruff format src/superseded/server/app.py tests/test_server_app.py`

- [ ] **Step 6: Commit**

```bash
git add src/superseded/server/app.py tests/test_server_app.py
git commit -m "feat: add POST /review/pr endpoint for Action-driven reviews"
```

---

## Task 4: Worker sandbox wiring (`SandboxSettings` + `ReviewJob.passes`)

**Files:**
- Modify: `src/superseded/server/worker.py`
- Test: `tests/test_server_worker.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server_worker.py`:

```python
@pytest.mark.asyncio
async def test_run_review_for_job_builds_sandbox_executor(tmp_path, monkeypatch):
    """When sandbox settings are enabled, _run_review_for_job builds a SandboxExecutor and passes it to engine.review."""
    from superseded.review.executor import SandboxExecutor
    from superseded.server.worker import SandboxSettings, _run_review_for_job

    github = FakeGitHubApp()
    repo_manager = FakeRepoManager()
    repo_manager.job_dir = MagicMock(return_value=tmp_path / "checkout")
    job = ReviewJob(1, "o", "r", 5, "abc", "def")

    captured: dict = {}
    mock_engine = MagicMock()
    mock_engine.review.return_value = MagicMock(findings=[], summary={})

    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/sbx")
    with (
        patch("superseded.server.worker.checkout_repo", new_callable=AsyncMock, return_value=tmp_path),
        patch("superseded.config.load_config", return_value=Config()),
        patch("superseded.review.engine.ReviewEngine.select", return_value=mock_engine),
        patch("superseded.context.gathering.compute_file_context", return_value=None),
        patch("superseded.context.gathering.run_static_analysis", return_value=None),
        patch("superseded.context.gathering.retrieve_usages", return_value=None),
    ):
        mock_engine.review.side_effect = lambda **kw: captured.update(kw) or MagicMock(findings=[], summary={})
        await _run_review_for_job(
            github=github,
            repo_manager=repo_manager,
            token="t",
            job=job,
            correlation_id="c",
            sandbox=SandboxSettings(enabled=True),
        )

    ex = captured.get("executor")
    assert isinstance(ex, SandboxExecutor)


@pytest.mark.asyncio
async def test_run_review_for_job_fails_when_sbx_missing(tmp_path, monkeypatch):
    """Sandbox enabled but sbx missing must raise a loud 'sandbox unavailable' error."""
    from superseded.server.worker import SandboxSettings, _run_review_for_job

    github = FakeGitHubApp()
    repo_manager = FakeRepoManager()
    repo_manager.job_dir = MagicMock(return_value=tmp_path / "checkout")
    job = ReviewJob(1, "o", "r", 5, "abc", "def")

    mock_engine = MagicMock()
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    with (
        patch("superseded.server.worker.checkout_repo", new_callable=AsyncMock, return_value=tmp_path),
        patch("superseded.config.load_config", return_value=Config()),
        patch("superseded.review.engine.ReviewEngine.select", return_value=mock_engine),
        patch("superseded.context.gathering.compute_file_context", return_value=None),
        patch("superseded.context.gathering.run_static_analysis", return_value=None),
        patch("superseded.context.gathering.retrieve_usages", return_value=None),
    ):
        with pytest.raises(RuntimeError, match="sandbox unavailable"):
            await _run_review_for_job(
                github=github,
                repo_manager=repo_manager,
                token="t",
                job=job,
                correlation_id="c",
                sandbox=SandboxSettings(enabled=True),
            )
    mock_engine.review.assert_not_called()


@pytest.mark.asyncio
async def test_run_review_for_job_defaults_no_sandbox(tmp_path):
    """Without sandbox settings, executor is NOT passed (engine uses SubprocessExecutor default)."""
    from superseded.server.worker import _run_review_for_job

    github = FakeGitHubApp()
    repo_manager = FakeRepoManager()
    repo_manager.job_dir = MagicMock(return_value=tmp_path / "checkout")
    job = ReviewJob(1, "o", "r", 5, "abc", "def")

    captured: dict = {}
    mock_engine = MagicMock()
    mock_engine.review.return_value = MagicMock(findings=[], summary={})
    with (
        patch("superseded.server.worker.checkout_repo", new_callable=AsyncMock, return_value=tmp_path),
        patch("superseded.config.load_config", return_value=Config()),
        patch("superseded.review.engine.ReviewEngine.select", return_value=mock_engine),
        patch("superseded.context.gathering.compute_file_context", return_value=None),
        patch("superseded.context.gathering.run_static_analysis", return_value=None),
        patch("superseded.context.gathering.retrieve_usages", return_value=None),
    ):
        mock_engine.review.side_effect = lambda **kw: captured.update(kw) or MagicMock(findings=[], summary={})
        await _run_review_for_job(
            github=github, repo_manager=repo_manager, token="t", job=job, correlation_id="c"
        )

    assert captured.get("executor") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server_worker.py -v`
Expected: FAIL (`SandboxSettings` undefined / `sandbox` kwarg unsupported).

- [ ] **Step 3: Implement**

FIRST read `src/superseded/server/worker.py` in full. Then:

(a) Add a `SandboxSettings` dataclass near the top of `worker.py` (after the existing `@dataclass class ReviewOutcome:` block):

```python
@dataclass
class SandboxSettings:
    """Whether/how the server runs agents inside sbx sandboxes."""

    enabled: bool = False
    binary: str = "sbx"
    timeout: int = 600
    keep_on_error: bool = False
    io_mode: str = "exec"
```

(b) Add a `passes` field to the `ReviewJob` dataclass:

```python
@dataclass
class ReviewJob:
    installation_id: int
    owner: str
    repo: str
    pr_number: int
    head_sha: str
    base_sha: str
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    passes: list[str] | None = None
```

(c) `ReviewWorker.__init__` gains a `sandbox` parameter (after `server_model`). Add to the signature:

```python
        server_model: str | None = None,
        sandbox: SandboxSettings | None = None,
```

and store it: `self._sandbox = sandbox`.

(d) In `ReviewWorker._process`, the call to `_run_review_for_job(...)` (around line 188–197) currently passes `server_agent=self.server_agent, server_model=self.server_model`. Add `sandbox=self._sandbox` to that call:

```python
            outcome = await _run_review_for_job(
                github=self.github,
                repo_manager=self.repo_manager,
                token=token,
                job=job,
                correlation_id=correlation_id,
                store=self.store,
                server_agent=self.server_agent,
                server_model=self.server_model,
                sandbox=self._sandbox,
            )
```

(e) `_run_review_for_job` gains a `sandbox: SandboxSettings | None = None` parameter (add it after `server_model: str | None = None,` in the signature). Then, right after the line `engine = ReviewEngine.select(config.agent, model=config.model, config=config)` (around line 434) and BEFORE the `result = await asyncio.to_thread(engine.review, ...)` call, insert executor construction:

```python
        executor = None
        if sandbox is not None and sandbox.enabled:
            from superseded.review.executor import make_sandbox_executor

            executor = make_sandbox_executor(
                agent_name=config.agent,
                name=f"superseded-{job.job_id}",
                timeout=sandbox.timeout,
                keep_on_error=sandbox.keep_on_error,
                binary=sandbox.binary,
                io_mode=sandbox.io_mode,
            )
            if not executor.available(engine.agent):
                raise RuntimeError(
                    f"sandbox unavailable: '{sandbox.binary}' not found on PATH "
                    "(install docker-sbx to run sandboxed reviews)."
                )
```

(f) In the `result = await asyncio.to_thread(engine.review, ...)` block (around lines 436–448), add two keyword arguments: `passes=job.passes,` (before `cwd=`) and `executor=executor,` (after `env=_server_env,`). The call does NOT pass `timeout` (the engine uses its own default). The final block must read exactly:

```python
        result = await asyncio.to_thread(
            engine.review,
            diff=diff,
            pr_description=pr_description,
            file_context=file_context,
            static_signals=static_signals,
            usage_signals=usage_signals,
            conventions_signals=conventions_signals,
            spec_signals=spec_signals,
            learned_context=learned_context,
            passes=job.passes,
            cwd=repo_path,
            env=_server_env,
            executor=executor,
        )
```

(Leave the surrounding code — the `_server_env = {...}` line above it and everything after it — unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server_worker.py -v`
Expected: PASS (all existing + 3 new).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest tests/ -q`
Expected: PASS (all). The existing `_run_review_for_job(...)` calls in tests don't pass `sandbox` → defaults to `None` → no sandbox → unchanged behavior.

- [ ] **Step 6: Lint and format**

Run: `uv run ruff check src/superseded/server/worker.py tests/test_server_worker.py && uv run ruff format src/superseded/server/worker.py tests/test_server_worker.py`

- [ ] **Step 7: Commit**

```bash
git add src/superseded/server/worker.py tests/test_server_worker.py
git commit -m "feat: wire SandboxExecutor into server worker"
```

---

## Task 5: `serve` command builds `SandboxSettings`

**Files:**
- Modify: `src/superseded/cli.py`

- [ ] **Step 1: Implement**

In `src/superseded/cli.py`, the `serve` command builds a `ReviewWorker` (around lines 782–789). First read that section. Add a `SandboxSettings` construction before the `worker = ReviewWorker(...)` call and pass it through. Add the import at the top of the `serve` function alongside the other server imports:

```python
    from superseded.server.worker import ReviewWorker, SandboxSettings
```

Then build and pass it:

```python
    sandbox = SandboxSettings(
        enabled=config.sandbox_enabled,
        binary=config.sbx_binary,
        timeout=config.sandbox_timeout,
        keep_on_error=config.sandbox_keep_on_error,
        io_mode=config.sandbox_io_mode,
    )
    worker = ReviewWorker(
        github=github,
        repo_manager=repo_manager,
        max_concurrent=config.max_concurrent_reviews,
        store=store,
        server_agent=config.agent,
        server_model=config.model,
        sandbox=sandbox,
    )
```

(The existing import line `from superseded.server.worker import ReviewWorker` becomes `from superseded.server.worker import ReviewWorker, SandboxSettings`. Replace the whole `worker = ReviewWorker(...)` block with the version above.)

- [ ] **Step 2: Verify (no new test — covered by worker tests; just ensure imports resolve and nothing breaks)**

Run: `uv run python -c "import superseded.cli"` then `uv run pytest tests/test_cli.py -q`
Expected: import succeeds; cli tests pass.

- [ ] **Step 3: Lint and format**

Run: `uv run ruff check src/superseded/cli.py && uv run ruff format src/superseded/cli.py`

- [ ] **Step 4: Commit**

```bash
git add src/superseded/cli.py
git commit -m "feat: pass SandboxSettings from ServerConfig to the review worker"
```

---

## Task 6: Rewrite `action.yml` as a composite action; remove `entrypoint.sh`

**Files:**
- Rewrite: `action.yml`
- Remove: `docker/entrypoint.sh`
- Modify: `docker/Dockerfile`
- Create: `tests/test_action.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_action.py`:

```python
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_action_is_composite():
    action = yaml.safe_load((REPO_ROOT / "action.yml").read_text())
    assert action["runs"]["using"] == "composite"
    assert "docker" not in action["runs"]
    assert "image" not in action["runs"]


def test_action_inputs_are_server_based():
    action = yaml.safe_load((REPO_ROOT / "action.yml").read_text())
    inputs = action["inputs"]
    assert "server-url" in inputs
    assert "server-key" in inputs
    # The old agent-credentials inputs are gone.
    assert "agent" not in inputs
    assert "model" not in inputs
    assert "anthropic_api_key" not in inputs
    assert "openai_api_key" not in inputs


def test_action_posts_to_review_pr():
    text = (REPO_ROOT / "action.yml").read_text()
    assert "/review/pr" in text
    assert "SUPERSEDED_SERVER_URL" in text
    assert "SUPERSEDED_SERVER_KEY" in text


def test_entrypoint_sh_removed():
    assert not (REPO_ROOT / "docker" / "entrypoint.sh").exists()


def test_dockerfile_no_longer_copies_entrypoint():
    dockerfile = (REPO_ROOT / "docker" / "Dockerfile").read_text()
    assert "entrypoint.sh" not in dockerfile
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_action.py -v`
Expected: FAIL (action.yml still docker-based; entrypoint.sh still present).

- [ ] **Step 3: Rewrite `action.yml`**

Replace the ENTIRE contents of `action.yml` with:

```yaml
name: "Superseded Code Review"
description: "Requests a multi-pass AI code review from a running Superseded server, which runs the agents in sandboxes and posts the review via its GitHub App. The App must be installed on the repository."
inputs:
  server-url:
    description: "Base URL of the running Superseded review server (e.g. https://reviews.example.com). Env SUPERSEDED_SERVER_URL overrides this."
    required: false
    default: ""
  server-key:
    description: "Bearer API key for the server. Env SUPERSEDED_SERVER_KEY overrides this. Map from a secret."
    required: false
    default: ""
  passes:
    description: "Comma-separated passes to run (optional; the server default applies if omitted)."
    required: false
    default: ""
runs:
  using: "composite"
  steps:
    - name: Request review
      shell: bash
      run: |
        set -euo pipefail
        URL="${SUPERSEDED_SERVER_URL:-${INPUT_SERVER_URL}}"
        KEY="${SUPERSEDED_SERVER_KEY:-${INPUT_SERVER_KEY}}"
        if [ -z "$URL" ] || [ -z "$KEY" ]; then
          echo "::error::Set SUPERSEDED_SERVER_URL and SUPERSEDED_SERVER_KEY (or the server-url/server-key inputs)." >&2
          exit 1
        fi
        if [ -z "${GITHUB_EVENT_PULL_REQUEST_NUMBER:-}" ]; then
          echo "::error::This action must run on a pull_request event." >&2
          exit 1
        fi
        owner=${GITHUB_REPOSITORY%/*}
        repo=${GITHUB_REPOSITORY#*/}
        body=$(jq -n --arg o "$owner" --arg r "$repo" --argjson n "$GITHUB_EVENT_PULL_REQUEST_NUMBER" '{owner:$o, repo:$r, pr_number:$n}')
        if [ -n "${INPUT_PASSES:-}" ]; then
          body=$(echo "$body" | jq --arg p "$INPUT_PASSES" '. + {passes:$p}')
        fi
        echo "Requesting review for PR #$GITHUB_EVENT_PULL_REQUEST_NUMBER at $URL/review/pr"
        curl -fsS --retry 3 --retry-delay 2 --retry-connrefused \
          -X POST "$URL/review/pr" \
          -H "Authorization: Bearer $KEY" \
          -H "Content-Type: application/json" \
          -d "$body"
```

- [ ] **Step 4: Remove `docker/entrypoint.sh` and update the Dockerfile**

Run: `rm docker/entrypoint.sh`

In `docker/Dockerfile`, in the `cli` stage, remove these two lines:

```
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
```

(The `cli` stage's `ENTRYPOINT ["superseded"]` and `CMD ["--help"]` stay. The comment block at the top of the file mentioning entrypoint can stay or be updated — at minimum the two COPY/RUN lines must go so the file no longer references `entrypoint.sh`.)

Also update the header comment in `docker/Dockerfile` that says "The GitHub Action builds `docker/Dockerfile`..." — replace that sentence with: "The GitHub Action is a composite action that POSTs to a running server; it no longer builds this image. The `cli` stage remains for containerized CLI use." (Keep the multi-target build instructions.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_action.py -v`
Expected: PASS (all 5).

- [ ] **Step 6: Lint and format**

Run: `uv run ruff check tests/test_action.py && uv run ruff format tests/test_action.py`

- [ ] **Step 7: Commit**

```bash
git add action.yml docker/Dockerfile tests/test_action.py
git commit -m "feat: rewrite GitHub Action as a composite client to the review server"
```

(Git will record the entrypoint.sh removal as part of this commit.)

---

## Task 7: `compose.yml` sandbox env

**Files:**
- Modify: `compose.yml`

- [ ] **Step 1: Implement**

In `compose.yml`, in the `api` service's `environment:` block, add (after the `SUPERSEDED_BEHIND_PROXY` line or alongside the other `SUPERSEDED_*` env lines):

```yaml
      SUPERSEDED_SANDBOX: ${SUPERSEDED_SANDBOX:-0}
```

(Default `0` because the containerized `api` service typically has no KVM; operators running the host/sandbox deployment set this via their own process manager, not compose.)

- [ ] **Step 2: Verify compose is still valid YAML**

Run: `uv run python -c "import yaml; yaml.safe_load(open('compose.yml'))" && echo OK`

- [ ] **Step 3: Commit**

```bash
git add compose.yml
git commit -m "feat: default SUPERSEDED_SANDBOX off in compose (no KVM in container)"
```

---

## Task 8: README breaking-change note

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the "GitHub Action" section**

In `README.md`, replace the existing `### GitHub Action` code block and its preceding lines (lines ~64–94, the `### GitHub Action` heading through the end of the first yaml block) with:

```markdown
### GitHub Action

The Action is a thin client: it POSTs the PR to a running Superseded server,
which runs the review in sandboxes and posts the result via its GitHub App.
**Breaking:** the Action no longer builds a Docker image or runs the agents
itself — you must (a) install the Superseded GitHub App on the repo and (b)
run the server somewhere reachable. No `permissions:` block is needed on the
workflow (the server's App does all GitHub writes).

```yaml
# .github/workflows/review.yml
name: Code Review
on:
  pull_request:
    types: [opened, synchronize]
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: fabioesposito/superseded@v1
        with:
          server-url: https://reviews.example.com
          server-key: ${{ secrets.SUPERSEDED_SERVER_KEY }}
          passes: security,correctness,performance
        env:
          # env vars override the inputs (useful for org-wide secrets):
          # SUPERSEDED_SERVER_URL: ...
          # SUPERSEDED_SERVER_KEY: ${{ secrets.SUPERSEDED_SERVER_KEY }}
```

Set `server-url`/`server-key` (or the `SUPERSEDED_SERVER_URL`/`SUPERSEDED_SERVER_KEY` env vars) and install the App on the repo. The previous `agent`, `model`, `anthropic_api_key`, and `openai_api_key` inputs are removed — the server owns agent/model/credentials.
```

(Leave the later "Using opencode with custom providers" subsection as-is — it still applies to the server/CLI. If it references Action inputs that no longer exist, add a one-line note that it applies to the server/CLI config, not the Action inputs.)

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document server-based GitHub Action (breaking)"
```

---

## Task 9: Full verification

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest tests/ -q`
Expected: PASS (all). Investigate any failure.

- [ ] **Step 2: Lint and format the whole project**

Run: `uv run ruff check src/ tests/ && uv run ruff format src/ tests/`
Expected: clean.

- [ ] **Step 3: Confirm the action.yml still parses and the CLI imports**

Run: `uv run python -c "import yaml; yaml.safe_load(open('action.yml')); import superseded.cli; print('ok')"`

- [ ] **Step 4: Final commit if formatting changed anything**

```bash
git add -A
git commit -m "test: full suite green for server endpoint + Action rewrite" || echo "nothing to commit"
```

---

## Self-review notes

- **Spec coverage:** Server `POST /review/pr` (owner/repo/pr_number + bearer key, installation resolution, 409/502/422/501/401), worker sandbox wiring with loud failure when `sbx` missing, `ServerConfig` sandbox fields + env, `SandboxSettings` threaded from `serve`, composite `action.yml` (env-over-input, fire-and-forget), `entrypoint.sh` removal + Dockerfile cleanup, `compose.yml` default sandbox off, README breaking note. All covered.
- **Behavior preservation:** Existing `_run_review_for_job(...)` and `ReviewWorker(...)` callers are unchanged because `sandbox`/`passes` default to `None`; webhook and manual `/review` paths are untouched; the containerized `api` server keeps working with `SUPERSEDED_SANDBOX=0`.
- **No placeholders:** every code step has complete code. The one "see note below — REMOVE" line in Task 4 is explicitly flagged for removal, not a placeholder.
- **Type consistency:** `SandboxSettings(enabled, binary, timeout, keep_on_error, io_mode)` matches the `ServerConfig` field names and the `make_sandbox_executor` kwargs; `ReviewJob.passes: list[str] | None` matches the `/review/pr` parsing and `engine.review(passes=...)`.
