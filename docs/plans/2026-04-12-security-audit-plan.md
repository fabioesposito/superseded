# Security Audit Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix all identified security vulnerabilities: command injection, path traversal, XSS, missing auth, missing CSRF, Docker hardening.

**Architecture:** Add a validation module for input sanitization, harden subprocess calls to use stdin, add optional API key auth + CSRF middleware, fix template escaping, and harden Dockerfile.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, aiosqlite, subprocess

---

### Task 1: Create validation module

**Files:**
- Create: `src/superseded/validation.py`
- Test: `tests/test_validation.py`

**Step 1: Write failing tests**

```python
# tests/test_validation.py
from __future__ import annotations

import pytest

from superseded.validation import (
    InvalidInput,
    sanitize_agent_prompt,
    validate_git_url,
    validate_issue_id,
    validate_repo_path,
)


class TestValidateIssueId:
    def test_valid_id(self):
        assert validate_issue_id("SUP-001") == "SUP-001"
        assert validate_issue_id("SUP-123") == "SUP-123"

    def test_rejects_traversal(self):
        with pytest.raises(InvalidInput):
            validate_issue_id("../../etc/passwd")

    def test_rejects_empty(self):
        with pytest.raises(InvalidInput):
            validate_issue_id("")

    def test_rejects_special_chars(self):
        with pytest.raises(InvalidInput):
            validate_issue_id("SUP-001; rm -rf /")


class TestValidateGitUrl:
    def test_https_url(self):
        assert validate_git_url("https://github.com/user/repo.git") == "https://github.com/user/repo.git"

    def test_ssh_url(self):
        assert validate_git_url("git@github.com:user/repo.git") == "git@github.com:user/repo.git"

    def test_rejects_shell_injection(self):
        with pytest.raises(InvalidInput):
            validate_git_url("https://example.com; rm -rf /")

    def test_rejects_file_protocol(self):
        with pytest.raises(InvalidInput):
            validate_git_url("file:///etc/passwd")

    def test_rejects_empty(self):
        with pytest.raises(InvalidInput):
            validate_git_url("")


class TestValidateRepoPath:
    def test_absolute_path(self, tmp_path):
        result = validate_repo_path(str(tmp_path))
        assert result == str(tmp_path)

    def test_rejects_relative(self):
        with pytest.raises(InvalidInput):
            validate_repo_path("relative/path")

    def test_rejects_traversal(self):
        with pytest.raises(InvalidInput):
            validate_repo_path("/foo/../../../etc")


class TestSanitizeAgentPrompt:
    def test_strips_null_bytes(self):
        assert sanitize_agent_prompt("hello\x00world") == "helloworld"

    def test_truncates_long(self):
        long = "a" * 200_000
        result = sanitize_agent_prompt(long)
        assert len(result) == 100_000
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_validation.py -v`
Expected: FAIL with "cannot import name"

**Step 3: Write minimal implementation**

```python
# src/superseded/validation.py
from __future__ import annotations

import re


class InvalidInput(ValueError):
    """Raised when user input fails validation."""


ISSUE_ID_RE = re.compile(r"^SUP-\d{3,}$")
GIT_URL_RE = re.compile(
    r"^(https://[a-zA-Z0-9._/:~-]+|git@[a-zA-Z0-9._-]+:[a-zA-Z0-9._/-]+\.git|ssh://[a-zA-Z0-9._/:@~-]+)$"
)


def validate_issue_id(value: str) -> str:
    value = value.strip()
    if not ISSUE_ID_RE.match(value):
        raise InvalidInput(f"Invalid issue ID: {value!r}")
    return value


def validate_git_url(value: str) -> str:
    value = value.strip()
    if not value:
        raise InvalidInput("Git URL cannot be empty")
    if not GIT_URL_RE.match(value):
        raise InvalidInput(f"Invalid git URL: {value!r}")
    return value


def validate_repo_path(value: str) -> str:
    from pathlib import Path

    p = Path(value)
    if not p.is_absolute():
        raise InvalidInput(f"Path must be absolute: {value!r}")
    resolved = p.resolve()
    # Check for traversal beyond root
    if ".." in value and str(resolved) != value:
        raise InvalidInput(f"Path traversal detected: {value!r}")
    return str(resolved)


MAX_PROMPT_LENGTH = 100_000


def sanitize_agent_prompt(value: str) -> str:
    value = value.replace("\x00", "")
    if len(value) > MAX_PROMPT_LENGTH:
        value = value[:MAX_PROMPT_LENGTH]
    return value
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_validation.py -v`
Expected: PASS

**Step 5: Lint**

Run: `uv run ruff check src/superseded/validation.py tests/test_validation.py`

**Step 6: Commit**

```bash
git add src/superseded/validation.py tests/test_validation.py
git commit -m "feat(security): add input validation module"
```

---

### Task 2: Apply validation to issue routes

**Files:**
- Modify: `src/superseded/routes/issues.py`

**Step 1: Add validation imports and apply to route handlers**

In `src/superseded/routes/issues.py`:

- Add import: `from superseded.validation import InvalidInput, validate_issue_id`
- Wrap `issue_id` parameter in `issue_detail` and `stage_detail` with `validate_issue_id()`
- Add error handling that returns 400 on `InvalidInput`

Changes to `issue_detail` (line 110-147):

```python
@router.get("/{issue_id}", response_class=HTMLResponse)
async def issue_detail(request: Request, issue_id: str, deps: Deps = Depends(get_deps)):
    try:
        issue_id = validate_issue_id(issue_id)
    except InvalidInput:
        return get_templates().TemplateResponse(
            request,
            "issue_detail.html",
            {"issue": None, "error": "Invalid issue ID", "stage_results": [], "stage_order": [s.value for s in Stage]},
            status_code=400,
        )
    # ... rest unchanged
```

Same pattern for `stage_detail` (line 150-198).

**Step 2: Lint**

Run: `uv run ruff check src/superseded/routes/issues.py`

**Step 3: Commit**

```bash
git add src/superseded/routes/issues.py
git commit -m "fix(security): validate issue_id in routes"
```

---

### Task 3: Apply validation to pipeline routes

**Files:**
- Modify: `src/superseded/routes/pipeline.py`

**Step 1: Add validation to pipeline route handlers**

In `src/superseded/routes/pipeline.py`:

- Add import: `from superseded.validation import InvalidInput, validate_issue_id`
- In `advance_issue` and `retry_issue`, wrap `issue_id` with `validate_issue_id()` before passing to `_find_issue`
- In `get_historical_events` and `stream_events`, validate `issue_id` similarly
- Return redirect to `/` on invalid input (consistent with existing pattern)

```python
@router.post("/issues/{issue_id}/advance")
async def advance_issue(request: Request, issue_id: str, deps: Deps = Depends(get_deps)):
    try:
        issue_id = validate_issue_id(issue_id)
    except InvalidInput:
        return RedirectResponse(url="/", status_code=303)
    # ... rest unchanged
```

Apply same pattern to `retry_issue`, `get_historical_events`, `stream_events`.

**Step 2: Lint**

Run: `uv run ruff check src/superseded/routes/pipeline.py`

**Step 3: Commit**

```bash
git add src/superseded/routes/pipeline.py
git commit -m "fix(security): validate issue_id in pipeline routes"
```

---

### Task 4: Apply validation to settings routes

**Files:**
- Modify: `src/superseded/routes/settings.py`

**Step 1: Add validation for git_url and path**

In `src/superseded/routes/settings.py`:

- Add import: `from superseded.validation import InvalidInput, validate_git_url, validate_repo_path`
- In `add_repo`, validate `git_url` (if non-empty) and `path` before creating `RepoEntry`
- Return error template on validation failure

```python
@router.post("/settings/repos", response_class=HTMLResponse)
async def add_repo(
    request: Request,
    deps: Deps = Depends(get_deps),
    name: str = Form(...),
    git_url: str = Form(""),
    path: str = Form(...),
    branch: str = Form(""),
):
    try:
        if git_url.strip():
            git_url = validate_git_url(git_url)
        path = validate_repo_path(path)
    except InvalidInput as e:
        return get_templates().TemplateResponse(
            request, "_repos_table.html", {"repos": deps.config.repos, "error": str(e)},
            status_code=400,
        )
    # ... rest unchanged
```

**Step 2: Lint**

Run: `uv run ruff check src/superseded/routes/settings.py`

**Step 3: Commit**

```bash
git add src/superseded/routes/settings.py
git commit -m "fix(security): validate git_url and path in settings"
```

---

### Task 5: Harden agent subprocess to use stdin

**Files:**
- Modify: `src/superseded/agents/base.py`
- Modify: `src/superseded/agents/claude_code.py`
- Modify: `src/superseded/agents/opencode.py`

**Step 1: Change agent adapters to read prompt from stdin**

In `src/superseded/agents/claude_code.py`:

```python
from __future__ import annotations

from superseded.agents.base import SubprocessAgentAdapter


class ClaudeCodeAdapter(SubprocessAgentAdapter):
    def _build_command(self, prompt: str) -> list[str]:
        return [
            "claude",
            "--print",
            "--output-format",
            "text",
        ]

    def _get_stdin_data(self, prompt: str) -> bytes | None:
        return prompt.encode("utf-8")
```

In `src/superseded/agents/opencode.py`:

```python
from __future__ import annotations

from superseded.agents.base import SubprocessAgentAdapter


class OpenCodeAdapter(SubprocessAgentAdapter):
    def _build_command(self, prompt: str) -> list[str]:
        return [
            "opencode",
            "--non-interactive",
        ]

    def _get_stdin_data(self, prompt: str) -> bytes | None:
        return prompt.encode("utf-8")
```

**Step 2: Update base class to support stdin**

In `src/superseded/agents/base.py`, add `_get_stdin_data` method and update `run` and `run_streaming`:

```python
def _get_stdin_data(self, prompt: str) -> bytes | None:
    """Override to pass prompt via stdin instead of CLI args."""
    return None
```

In `run()` method (line 33-53), change the subprocess call:

```python
async def run(self, prompt: str, context: AgentContext) -> AgentResult:
    cmd = self._build_command(prompt)
    cwd = self._get_cwd(context)
    stdin_data = self._get_stdin_data(prompt)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE if stdin_data else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=stdin_data), timeout=self.timeout
        )
        # ... rest unchanged
```

Same change in `run_streaming()` method (line 55+).

**Step 3: Add security test for prompt injection**

```python
# tests/test_agents.py - add test
def test_prompt_not_in_argv():
    """Prompt should be passed via stdin, not as a CLI argument."""
    from superseded.agents.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter()
    cmd = adapter._build_command("malicious; rm -rf /")
    # Prompt should NOT appear in command args
    assert "malicious; rm -rf /" not in cmd
    # But stdin data should contain it
    assert adapter._get_stdin_data("malicious; rm -rf /") == b"malicious; rm -rf /"
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_agents.py -v`

**Step 5: Lint**

Run: `uv run ruff check src/superseded/agents/`

**Step 6: Commit**

```bash
git add src/superseded/agents/base.py src/superseded/agents/claude_code.py src/superseded/agents/opencode.py tests/test_agents.py
git commit -m "fix(security): pass agent prompts via stdin instead of CLI args"
```

---

### Task 6: Verify template XSS safety

**Files:**
- Review: `templates/issue_detail.html`
- Review: `templates/issue_new.html`
- Review: `templates/settings.html`

**Step 1: Audit all template interpolations**

Check every `{{ ... }}` in templates:

- `issue_detail.html:18` — `{{ issue.title }}` — autoescaped ✓
- `issue_detail.html:130` — `{{ result.error }}` — autoescaped ✓
- `issue_detail.html:144` — `{{ iter.error }}` — autoescaped ✓
- `issue_detail.html:172` — `var issueId = '{{ issue.id }}'` — validated to `SUP-\d+` ✓
- `issue_new.html:17` — `{{ github_url or '' }}` — in input value attribute, autoescaped ✓
- `issue_new.html:33` — `{{ title or '' }}` — autoescaped ✓
- `issue_new.html:41` — `{{ body or '' }}` — in textarea, autoescaped ✓

All template interpolations are in HTML context where Jinja2 autoescaping applies. The `issue.id` in JS context is validated to match `SUP-\d+` which is safe.

**Step 2: Verify autoescaping is enabled**

Check `src/superseded/routes/__init__.py` — uses `Jinja2Templates` which enables autoescaping by default.

No changes needed. Document findings.

**Step 3: Commit (if any changes made)**

If no changes needed, skip commit.

---

### Task 7: Add API key authentication middleware

**Files:**
- Modify: `src/superseded/config.py`
- Create: `src/superseded/routes/auth.py`
- Modify: `src/superseded/main.py`
- Test: `tests/test_auth.py`

**Step 1: Write failing tests**

```python
# tests/test_auth.py
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from superseded.main import create_app
from superseded.config import SupersededConfig


@pytest.fixture
def tmp_repo():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        issues_dir = repo / ".superseded" / "issues"
        issues_dir.mkdir(parents=True)
        yield str(repo)


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
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_auth.py -v`
Expected: FAIL (config has no api_key field, middleware doesn't exist)

**Step 3: Add api_key to config**

In `src/superseded/config.py`, add field to `SupersededConfig`:

```python
api_key: str = ""
```

**Step 4: Create auth middleware**

```python
# src/superseded/routes/auth.py
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

EXEMPT_PATHS = {"/health", "/static"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        api_key = getattr(request.app.state, "config", None)
        if api_key is None:
            return await call_next(request)

        api_key = request.app.state.config.api_key
        if not api_key:
            return await call_next(request)

        path = request.url.path
        if path in EXEMPT_PATHS or path.startswith("/static/"):
            return await call_next(request)

        provided = request.headers.get("X-API-Key", "")
        if provided != api_key:
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})

        return await call_next(request)
```

**Step 5: Register middleware in main.py**

In `src/superseded/main.py`, add after app creation:

```python
from superseded.routes.auth import AuthMiddleware
app.add_middleware(AuthMiddleware)
```

**Step 6: Run tests**

Run: `uv run pytest tests/test_auth.py -v`

**Step 7: Lint**

Run: `uv run ruff check src/superseded/routes/auth.py src/superseded/config.py src/superseded/main.py tests/test_auth.py`

**Step 8: Commit**

```bash
git add src/superseded/routes/auth.py src/superseded/config.py src/superseded/main.py tests/test_auth.py
git commit -m "feat(security): add optional API key authentication"
```

---

### Task 8: Add CSRF protection

**Files:**
- Create: `src/superseded/routes/csrf.py`
- Modify: `src/superseded/main.py`
- Modify: `templates/base.html` (add CSRF meta tag)
- Test: `tests/test_csrf.py`

**Step 1: Write failing tests**

```python
# tests/test_csrf.py
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
        resp = await client.post(
            "/issues/new",
            data={"title": "test", "body": "test"},
            headers={"X-CSRF-Token": csrf_token},
            follow_redirects=False,
        )
        assert resp.status_code == 303
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_csrf.py -v`
Expected: FAIL (403 is not returned, CSRF not implemented)

**Step 3: Create CSRF middleware**

```python
# src/superseded/routes/csrf.py
from __future__ import annotations

import hashlib
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
EXEMPT_PATHS = {"/health", "/static"}


def _generate_csrf_token() -> str:
    return secrets.token_hex(32)


class CsrfMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip CSRF for exempt paths and static files
        if path in EXEMPT_PATHS or path.startswith("/static/"):
            return await call_next(request)

        # Skip CSRF if API key auth is used
        api_key = getattr(request.app.state, "config", None)
        if api_key and api_key.api_key and request.headers.get("X-API-Key"):
            return await call_next(request)

        # Safe methods don't need CSRF
        if request.method in SAFE_METHODS:
            response = await call_next(request)
            # Set CSRF cookie on GET requests if not present
            if request.method == "GET" and "csrf_token" not in request.cookies:
                token = _generate_csrf_token()
                response.set_cookie("csrf_token", token, httponly=False, samesite="lax")
            return response

        # Validate CSRF token on unsafe methods
        csrf_cookie = request.cookies.get("csrf_token", "")
        csrf_header = request.headers.get("X-CSRF-Token", "")

        if not csrf_cookie or csrf_header != csrf_cookie:
            return JSONResponse(status_code=403, content={"error": "CSRF validation failed"})

        return await call_next(request)
```

**Step 4: Register middleware in main.py**

Add after AuthMiddleware registration:

```python
from superseded.routes.csrf import CsrfMiddleware
app.add_middleware(CsrfMiddleware)
```

**Step 5: Add CSRF meta tag to base template**

In `templates/base.html`, add in `<head>`:

```html
<meta name="csrf-token" content="{{ request.cookies.get('csrf_token', '') }}">
```

And add a script to include the token in HTMX requests:

```html
<script>
document.body.addEventListener('htmx:configRequest', function(evt) {
    var token = document.querySelector('meta[name="csrf-token"]');
    if (token) evt.detail.headers['X-CSRF-Token'] = token.content;
});
</script>
```

**Step 6: Run tests**

Run: `uv run pytest tests/test_csrf.py -v`

**Step 7: Lint**

Run: `uv run ruff check src/superseded/routes/csrf.py src/superseded/main.py tests/test_csrf.py`

**Step 8: Commit**

```bash
git add src/superseded/routes/csrf.py src/superseded/main.py templates/base.html tests/test_csrf.py
git commit -m "feat(security): add CSRF protection middleware"
```

---

### Task 9: Harden Dockerfile

**Files:**
- Modify: `Dockerfile`

**Step 1: Add non-root user and harden**

```dockerfile
FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
RUN chown -R appuser:appgroup /app
USER appuser

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "superseded.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--factory"]
```

**Step 2: Commit**

```bash
git add Dockerfile
git commit -m "fix(security): run container as non-root user"
```

---

### Task 10: Run full test suite and lint

**Step 1: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All PASS

**Step 2: Run lint**

Run: `uv run ruff check src/ tests/`
Expected: No errors

**Step 3: Run format check**

Run: `uv run ruff format --check src/ tests/`
Expected: No changes needed

**Step 4: Final commit if needed**

If any fixes needed from lint/test failures, commit them.
