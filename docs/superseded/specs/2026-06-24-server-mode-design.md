# Server Mode Design

**Date:** 2026-06-24
**Status:** Approved
**Approach:** FastAPI + in-process queue

## Overview

Add a server mode to Superseded that runs as a GitHub App, receiving webhook events and running AI code reviews on a dedicated server. This replaces the Docker-based GitHub Action with a persistent server that multiple repos can install.

## Goals

- Single dedicated server running Superseded as a persistent service
- GitHub App integration (webhook receiver + GitHub API client)
- Multi-repo, multi-org support (users install the app on their repos)
- Configurable concurrency for parallel reviews
- Status check reporting on PRs
- Graceful lifecycle management

## Architecture

### End-to-End Flow

```
GitHub PR opened/updated
  ↓
POST /webhook (verify signature, return 200 immediately)
  ↓
asyncio.Queue (buffer jobs)
  ↓
ReviewWorker (semaphore-limited concurrency)
  ↓
GitHubApp.get_installation_token() (JWT → installation token)
  ↓
RepoManager.clone() (shallow clone to /tmp)
  ↓
resolve_config() (read .superseded.yaml from repo)
  ↓
ReviewEngine.review() (existing code, unchanged)
  ↓
GitHubApp.post_review() (PR comments via API)
  ↓
GitHubApp.create_check_run() (status check)
  ↓
RepoManager.cleanup() (delete temp dir)
```

### Module Layout

New modules under `src/superseded/server/`:

```
src/superseded/server/
├── __init__.py
├── app.py              # FastAPI app, routes, middleware
├── github.py           # GitHubApp: auth, API calls, webhook verification
├── checkout.py         # Shallow clone, file fetching
├── repo_manager.py     # Temp dir lifecycle, disk management
├── worker.py           # ReviewWorker: queue consumer, concurrency control
├── lifecycle.py        # Startup, shutdown, health checks
└── config.py           # Server-specific config (env vars, secrets)
```

## Components

### 1. GitHub App Integration (`github.py`)

**Authentication flow:**

1. App private key signs a JWT (algorithm: RS256)
2. JWT used to create installation access token via `POST /app/installations/{id}/access_tokens`
3. Installation token used for all API calls (short-lived, ~1 hour)

**Webhook verification:**

- HMAC-SHA256 signature verification using `X-Hub-Signature-256` header
- Timing-safe comparison to prevent timing attacks

**API client methods:**

- `verify_webhook(payload, signature) -> bool`
- `get_installation_token(installation_id) -> str`
- `fetch_pr_diff(token, owner, repo, pr_number) -> str`
- `fetch_pr_description(token, owner, repo, pr_number) -> str`
- `post_review(token, owner, repo, pr_number, findings) -> None`
- `create_check_run(token, owner, repo, sha, conclusion, title, summary) -> int`

**Permissions required:**

- `pull_requests: write` (post reviews)
- `checks: write` (create/update check runs)
- `contents: read` (fetch repo contents)
- `metadata: read` (repo metadata)

**Subscribed events:**

- `pull_request` (opened, synchronize, reopened)
- `push` (optional: re-review on push)
- `installation` (created, deleted)

### 2. HTTP Server (`app.py`)

**Endpoints:**

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `POST /webhook` | Receive GitHub webhook events | Webhook signature |
| `GET /health` | Health check for monitoring | None |
| `POST /review` | Manual/ad-hoc review trigger | API key (future work) |

**Webhook handler flow:**

1. Verify `X-Hub-Signature-256` header
2. Parse event type from `X-GitHub-Event` header
3. Route by event type:
   - `pull_request` → `handle_pr_event()`
   - `push` → `handle_push_event()`
   - `installation` → `handle_installation_event()`
4. Return HTTP 200 immediately (don't block GitHub's 10s timeout)
5. Enqueue job to `asyncio.Queue`

**PR event handling:**

- `opened`, `synchronize`, `reopened` → enqueue review
- `closed` → cleanup (optional)

**Installation event handling:**

- `created` → store installation in database
- `deleted` → remove installation from database

### 3. Repo Checkout (`checkout.py`)

**Shallow clone approach:**

```python
async def checkout_repo(
    token: str,
    owner: str,
    repo: str,
    ref: str,           # branch/SHA to checkout
    base_ref: str,      # base branch for diff
    tmp_dir: str        # /tmp/superseded/{installation_id}/{repo}
) -> Path:
```

- Uses `git clone --depth=2 --branch {ref}` with installation token for auth
- `ref` = PR head SHA from webhook payload (`pull_request.head.sha`)
- `base_ref` = PR base branch from webhook payload (`pull_request.base.sha`)
- Returns path to checked out repo
- Shallow clone is sufficient for diff computation and file reading

**Config resolution:**

```python
async def resolve_config(repo_path: Path, owner: str, repo: str) -> Config:
```

- Reads `.superseded.yaml` from repo root (via filesystem, not API)
- Falls back to server-side defaults if not present
- Per-installation config overrides supported

### 4. Repo Manager (`repo_manager.py`)

**Temp directory lifecycle:**

- Base path: `/tmp/superseded/{installation_id}/{repo}/{pr_number}/`
- Created on clone, deleted after review completes (or on failure)
- Disk space check before clone (skip if > 90% full)

**Methods:**

- `clone(installation_id, owner, repo, ref, base_ref) -> Path`
- `cleanup(path: Path) -> None`
- `disk_usage() -> float` (0.0 to 1.0)

### 5. Review Worker (`worker.py`)

**Queue consumer:**

```python
class ReviewWorker:
    async def run(self):
        while True:
            job = await self.queue.get()
            try:
                await self._process(job)
            finally:
                self.queue.task_done()
```

**Concurrency control:**

- `MAX_CONCURRENT_REVIEWS` configurable (default: 3)
- `asyncio.Semaphore` limits parallel reviews
- Excess jobs wait in queue

**Job processing:**

1. Create "in_progress" check run on GitHub
2. Get installation access token
3. Clone repo (shallow)
4. Read config from repo
5. Run review (reuse existing `ReviewEngine`)
6. Post results as PR review comments
7. Update check run to "completed"
8. Cleanup temp directory

### 6. Status Reporting

**Check run lifecycle:**

- Created with status "in_progress" when review starts
- Updated to "completed" with conclusion:
  - `"success"` if no critical/important findings
  - `"failure"` if critical or important findings exist
- Title: `"3 findings (1 critical, 1 important, 1 suggestion)"`
- Summary: human-readable breakdown

**PR review posting:**

- Reuses existing `post_review_to_pr()` logic from `output/github_pr.py`
- Refactored to use `GitHubApp` client instead of `gh` CLI subprocess
- Maps `Finding.file` + `Finding.line` to GitHub's inline comment format
- Links comment IDs back to findings in memory store

### 7. Lifecycle Management (`lifecycle.py`)

**Graceful shutdown:**

1. Receive SIGTERM
2. Stop accepting new webhooks
3. Wait for in-flight reviews to complete (configurable timeout)
4. Log unprocessed jobs in queue
5. Close database connections
6. Exit 0

**Health check endpoint:**

```python
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "queue_depth": worker.queue.qsize(),
        "active_reviews": worker.active_count,
        "disk_usage": repo_manager.disk_usage(),
        "uptime_seconds": time.time() - start_time,
    }
```

### 8. Observability

**Structured logging:**

- JSON formatted logs for easy parsing
- Each review gets a correlation ID (UUID)
- Log events: `review_started`, `review_completed`, `review_failed`, `webhook_received`

**Error tracking:**

- Errors include correlation ID for debugging
- Check run shows error details if review fails
- Failed reviews logged with full context

## Configuration

### Server Config File

`/etc/superseded/server.yaml` or environment variables:

```yaml
app_id: 12345
private_key_path: /path/to/key.pem
webhook_secret: whsec_...
max_concurrent_reviews: 3
temp_dir: /tmp/superseded
log_level: info

# Default settings for repos without .superseded.yaml
defaults:
  agent: claude-code
  model: claude-sonnet-4-20250514
  passes: [security, correctness, performance, style, architecture]
```

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `SUPERSEDED_APP_ID` | GitHub App ID |
| `SUPERSEDED_PRIVATE_KEY_PATH` | Path to private key PEM |
| `SUPERSEDED_WEBHOOK_SECRET` | Webhook signature secret |
| `SUPERSEDED_MAX_CONCURRENT` | Max parallel reviews |
| `SUPERSEDED_PORT` | Server port (default: 8000) |
| `SUPERSEDED_HOST` | Server host (default: 0.0.0.0) |

### CLI Command

```bash
superseded serve --port 8000 --host 0.0.0.0
superseded serve --config /etc/superseded/server.yaml
```

## Database Changes

**New table: `installations`**

```sql
CREATE TABLE installations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_installation_id INTEGER UNIQUE NOT NULL,
    owner TEXT NOT NULL,
    repos TEXT NOT NULL,  -- JSON array of repo names
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Note: The GitHub App private key is app-level (stored in server config), not per-installation.

**Per-installation config overrides** (optional, for future use):

```sql
CREATE TABLE installation_config (
    installation_id INTEGER REFERENCES installations(id),
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (installation_id, key)
);
```

## Dependencies

**New runtime dependencies (optional `[server]` extra):**

| Package | Version | Purpose |
|---------|---------|---------|
| `fastapi` | >=0.115.0 | HTTP framework |
| `uvicorn` | >=0.34.0 | ASGI server |
| `pyjwt` | >=2.10.0 | JWT signing for GitHub App auth |

**Dev dependencies:**

- `httpx` already available (used for testing, now used for GitHub API)

**Changes to existing modules:**

- `cli.py`: Add `superseded serve` command
- `diff.py`: Extract diff logic into functions accepting both local path and API-fetched diff
- `review/engine.py`: Accept config from server context instead of CLI args
- `output/github_pr.py`: Refactor to use `GitHubApp` client instead of `gh` CLI subprocess
- `pyproject.toml`: Add `[server]` optional dependency group

## Security Considerations

- Webhook signature verification (HMAC-SHA256, timing-safe)
- Installation tokens scoped to specific repos
- Private keys stored securely (file permissions, not in env vars)
- No secrets in logs (correlation IDs only)
- Disk space limits prevent abuse
- Concurrency limits prevent resource exhaustion

## Testing Strategy

- Unit tests for GitHub App auth (JWT signing, token creation)
- Unit tests for webhook verification
- Unit tests for repo checkout (mock git commands)
- Integration tests for full review flow (mock GitHub API)
- Load testing for concurrency limits

## Future Considerations

- Redis queue for crash recovery (replace asyncio.Queue)
- Web dashboard for installation management
- Per-installation billing/usage tracking
- Support for multiple AI agent backends per installation
- Caching of unchanged files between reviews
