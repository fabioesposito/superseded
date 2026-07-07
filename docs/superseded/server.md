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
agent: null                           # null = use repo's .superseded.yaml
model: null                           # null = use repo's .superseded.yaml
```

Environment variables (prefixed with `SUPERSEDED_`):

| Variable | Config Field | Required | Default |
|---|---|---|---|
| `SUPERSEDED_APP_ID` | `app_id` | Yes | — |
| `SUPERSEDED_WEBHOOK_SECRET` | `webhook_secret` | Yes | — |
| `SUPERSEDED_PRIVATE_KEY_PATH` | `private_key_path` | Yes | — |
| `SUPERSEDED_PORT` | `port` | No | `8000` |
| `SUPERSEDED_HOST` | `host` | No | `127.0.0.1` |
| `SUPERSEDED_BEHIND_PROXY` | `behind_proxy` | No | `false` |
| `SUPERSEDED_MAX_CONCURRENT` | `max_concurrent_reviews` | No | `3` |
| `SUPERSEDED_LOG_LEVEL` | `log_level` | No | `info` |
| `SUPERSEDED_HEALTH_TOKEN` | `health_token` | No | — |
| `SUPERSEDED_DATABASE_URL` | `database_url` | No | SQLite |
| `SUPERSEDED_TLS_CERT` | `tls_cert_path` | No | — |
| `SUPERSEDED_TLS_KEY` | `tls_key_path` | No | — |
| `SUPERSEDED_SERVER_AGENT` | `agent` | No | None |
| `SUPERSEDED_SERVER_MODEL` | `model` | No | None |

Launch:

```bash
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

The server clones the repo into `/tmp/superseded/` (or your configured `temp_dir`), checks out the PR head, and runs the review. Results appear as a GitHub Check Run on the PR with findings listed.

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

### Docker / Compose Deployment

Ship the server as a container backed by Postgres. The image is the `api` target
of the multi-stage `docker/Dockerfile`; `compose.yml` wires it to a Postgres
service.

```bash
cp .env.example .env                       # fill in the required values
mkdir -p keys && cp /path/to/private-key.pem keys/private-key.pem
docker compose up -d                       # api + postgres
```

`.env` holds the non-file secrets; the GitHub App private key is mounted
read-only from `./keys/private-key.pem` (never baked into the image or stored in
`.env`). Required `.env` values: `POSTGRES_PASSWORD`, `SUPERSEDED_APP_ID`,
`SUPERSEDED_WEBHOOK_SECRET`.

The API binds `0.0.0.0:8000` **inside the compose network only** — there is no
published port. Terminate TLS at a reverse proxy in front of compose (nginx,
Caddy, Traefik) and forward to the `api` service. Because the bind is
non-loopback without in-process TLS, set `SUPERSEDED_BEHIND_PROXY=1` (compose
sets this for you): it tells the server that TLS terminates upstream, relaxing
the otherwise-strict "non-loopback requires TLS" guard. (Direct public binds
without TLS still raise at startup — the guard is only relaxed when
`behind_proxy` is explicitly enabled.)

**Build the images directly** (without compose):

```bash
# CLI image (all three AI CLIs + gh) — also what the GitHub Action builds.
docker build -f docker/Dockerfile --target cli -t superseded-cli .

# Server image.
docker build -f docker/Dockerfile --target api -t superseded-api .

# Slim image carrying only the agent you actually run (~1 GB vs ~2 GB).
docker build -f docker/Dockerfile --build-arg AI_CLIS=@anthropic-ai/claude-code \
    --target api -t superseded-api-claude .
```

`AI_CLIS` is a space-separated list of npm package specs; the default installs
`@anthropic-ai/claude-code @openai/codex opencode-ai`. Override it via
`--build-arg`, or in compose via the `AI_CLIS` variable in `.env`.

> The server never invokes `gh` — it uses the GitHub REST API directly (and
> clones via `git`) — so `gh` is installed only in the CLI image, not the server
> image.

## Smolvm Sandbox

The `smolvm` sandbox backend boots agent OCI images as microVMs. Set `SUPERSEDED_SANDBOX_KIND=smolvm` and provide per-agent images via env vars.

### Image Boot Sources

Images resolve to one of three boot sources:

| Source | Trigger | Example | Overhead |
|---|---|---|---|
| `file` | Path on disk exists | `SUPERSEDED_SMOLVM_IMAGE=/tmp/opencode.tar` | Minimal — reads archive directly |
| `docker` | Bare local Docker tag | `SUPERSEDED_SMOLVM_IMAGE=superseded-smolvm-opencode:latest` | ~5s of `docker save` per run (re-serializes image) |
| `registry` | Anything else (OCI ref) | `SUPERSEDED_SMOLVM_IMAGE=ghcr.io/org/opencode:latest` | Registry pull latency at boot |

### Startup Benchmarks

Measured with `superseded-smolvm-opencode:latest` (808 MB Docker image) on a KVM-capable Linux host:

| Boot source | Create | Start (VM boot) | Total lifecycle |
|---|---|---|---|
| docker pipe | 4.9s | 9.5s | 16.3s |
| file path | 0.8s | 9.1s | 10.7s |
| registry (SDK) | — | — | (not benchmarked) |

The VM boot itself (~9s) is the dominant fixed cost. The docker pipe adds ~5s of `docker save` overhead because the image is re-serialized on every run even though it hasn't changed.

### Optimizing Startup

Pre-export the image to a tar file once, then point at the file path:

```bash
docker save superseded-smolvm-opencode:latest -o /var/lib/superseded/images/opencode.tar
export SUPERSEDED_SMOLVM_IMAGE_OPENCODE=/var/lib/superseded/images/opencode.tar
```

This cuts startup from ~16s to ~11s by skipping the `docker save` re-serialization.

### Per-Agent Vars

| Env var | Agent |
|---|---|
| `SUPERSEDED_SMOLVM_IMAGE_CLAUDE_CODE` | claude-code |
| `SUPERSEDED_SMOLVM_IMAGE_OPENCODE` | opencode |
| `SUPERSEDED_SMOLVM_IMAGE_CODEX` | codex |
| `SUPERSEDED_SMOLVM_IMAGE` | Fallback for all agents |

## GitHub Action

The GitHub Action (`action.yml`) is a **composite** Action — a single `curl` step
that POSTs `{owner, repo, pr_number, passes?}` to your running review server's
`/review/pr` endpoint. The server runs the agents in sandboxes and posts the
review via its GitHub App. No Docker image is built in CI and no agent
credentials live on the runner — the server owns agent/model/credentials.

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
