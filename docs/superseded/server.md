# Server Mode & GitHub Integration

Superseded can run as a **background server** that automatically reviews pull requests via GitHub webhooks, or be triggered **on-demand from CI** via a composite GitHub Action that hands the PR off to a running server.

## Server Mode

`superseded serve` boots a FastAPI server that listens for GitHub webhooks and runs reviews automatically.

### Setup

You'll need a GitHub App with permissions to read code and write check runs / pull request comments.

Create a server config file:

```yaml
# superseded-server.yaml
app_id: 123456
webhook_secret: "your-webhook-secret"
private_key_path: "/etc/superseded/github-app.pem"
port: 8000
host: "0.0.0.0"
max_concurrent_reviews: 3
temp_dir: "/tmp/superseded"
log_level: "info"
database_url: null                    # null = SQLite, or postgresql://...
provider: deepseek                    # provider is fixed server-side
model: null                           # null = use repo's .superseded.yaml
deepseek_api_key: "sk-..."            # or set SUPERSEDED_DEEPSEEK_API_KEY
```

Environment variables (prefixed with `SUPERSEDED_`):

| Variable | Config Field | Required | Default |
|---|---|---|---|
| `SUPERSEDED_APP_ID` | `app_id` | Yes | — |
| `SUPERSEDED_WEBHOOK_SECRET` | `webhook_secret` | Yes | — |
| `SUPERSEDED_PRIVATE_KEY_PATH` | `private_key_path` | Yes | — |
| `SUPERSEDED_DEEPSEEK_API_KEY` | `deepseek_api_key` | Yes | — |
| `SUPERSEDED_PORT` | `port` | No | `8000` |
| `SUPERSEDED_HOST` | `host` | No | `127.0.0.1` |
| `SUPERSEDED_BEHIND_PROXY` | `behind_proxy` | No | `false` |
| `SUPERSEDED_MAX_CONCURRENT` | `max_concurrent_reviews` | No | `3` |
| `SUPERSEDED_LOG_LEVEL` | `log_level` | No | `info` |
| `SUPERSEDED_HEALTH_TOKEN` | `health_token` | No | — |
| `SUPERSEDED_API_KEY` | `api_key` | No | — |
| `SUPERSEDED_DATABASE_URL` | `database_url` | No | SQLite |
| `SUPERSEDED_TLS_CERT` | `tls_cert_path` | No | — |
| `SUPERSEDED_TLS_KEY` | `tls_key_path` | No | — |
| `SUPERSEDED_SERVER_MODEL` | `model` | No | None |

`SUPERSEDED_DEEPSEEK_API_KEY` is the key for the DeepSeek API — the server
refuses to start without it. `SUPERSEDED_API_KEY` (optional) is the bearer key
for the `/review/pr` endpoint that the GitHub Action calls (the Action's
`server-key` input). Upgrading from v0.5.x? See [MIGRATION.md](../../MIGRATION.md).

Launch:

```bash
export SUPERSEDED_DEEPSEEK_API_KEY=sk-...   # required — the server refuses to start without it
superseded serve --config superseded-server.yaml
# or with overrides
superseded serve --port 9000 --host 0.0.0.0
# with TLS
SUPERSEDED_TLS_CERT=/etc/ssl/cert.pem SUPERSEDED_TLS_KEY=/etc/ssl/key.pem superseded serve
```

### Webhook Events

| Event | Behavior |
|---|---|
| `pull_request` (opened, synchronize, reopened) | Clones repo, runs review, posts as GitHub Check Run |
| `installation` (created) | Stores installation info (owner, repos) |
| `installation` (deleted) | Removes installation record |
| `push` | Logged only |

The server clones the repo into `/tmp/superseded/` (or your configured `temp_dir`), checks out the PR head, and runs the review. Results appear as a GitHub Check Run on the PR with findings listed. The server never invokes `gh` — it talks to the GitHub REST API directly and clones via `git`.

### Security

- Config is loaded from the **default branch**, not from the PR head (prevents malicious `.superseded.yaml` injection)
- `static_analysis` and the `security` pass are **forced on** in server mode regardless of config
- Git clone tokens are passed via environment variables, never as CLI arguments
- Tokens are redacted from error messages
- Path traversal is protected against in all file operations
- Disk usage is monitored — reviews stop at 90% disk usage (configurable)

### Rate Limiting & Concurrency

- Rate limited: 60 requests per 60 seconds per IP
- Replay protection: 300-second window (ignores duplicate webhooks)
- Job queue: 100 pending maximum (returns 429 when full)
- Concurrent reviews: configurable semaphore (default 3)

### Endpoints

| Path | Purpose |
|---|---|
| `POST /webhook` | GitHub webhook receiver |
| `GET /health` | Health check (optional token auth) |

### PostgreSQL Backend

For production deployments, set `SUPERSEDED_DATABASE_URL` to a PostgreSQL connection string:

```bash
SUPERSEDED_DATABASE_URL=postgresql://user:pass@host:5432/superseded
```

This enables concurrent access from multiple server instances. The SQLite path is fine for single-instance deployments.

On startup, `superseded serve` runs pending Alembic migrations against the configured database (SQLite or Postgres) before serving requests. To run or inspect migrations deliberately — e.g. ahead of a deploy, or to diagnose a stuck migration — run `superseded migrate` (honors `SUPERSEDED_DATABASE_URL`, or pass `--database-url`). Pre-existing databases from older versions are auto-adopted on first run with no data loss.

## GitHub Action

The GitHub Action (`action.yml`) is a **composite** Action — a single `curl` step
that POSTs `{owner, repo, pr_number, passes?}` to your running review server's
`/review/pr` endpoint. The server calls the DeepSeek API directly and posts the
review via its GitHub App. No Docker image is built in CI and no credentials
live on the runner — the server owns provider/model/credentials, and the DeepSeek
API key (`SUPERSEDED_DEEPSEEK_API_KEY`) lives in the server's environment only;
the Action never receives it.

### Usage

```yaml
# .github/workflows/review.yml
name: Code Review
on:
  pull_request:
    types: [opened, synchronize, reopened]
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
          # SUPERSEDED_SERVER_URL: https://reviews.example.com
          # SUPERSEDED_SERVER_KEY: ${{ secrets.SUPERSEDED_SERVER_KEY }}
```

### Action Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `server-url` | No* | — | Base URL of the running review server |
| `server-key` | No* | — | Bearer API key for the `/review/pr` endpoint (map from a secret) |
| `passes` | No | — | Comma-separated passes to run (the server default applies if omitted) |

\* `server-url` and `server-key` are each optional as inputs but one way or
another both must be supplied — the step fails if either is empty.

`SUPERSEDED_SERVER_URL` / `SUPERSEDED_SERVER_KEY` env vars override the
`server-url` / `server-key` inputs. The PR number is read from the
`GITHUB_EVENT_PULL_REQUEST_NUMBER` variable GitHub sets automatically; no
`permissions:` block is needed because the server's App performs all GitHub
writes.
