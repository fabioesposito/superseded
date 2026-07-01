# Server Mode & GitHub Integration

Superseded can run as a **background server** that automatically reviews pull requests via GitHub webhooks, or as a **one-shot GitHub Action** in your CI pipeline.

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

## GitHub Action

Superseded is packaged as a Docker-based GitHub Action (`action.yml`).

### Usage

```yaml
name: AI Code Review
on:
  pull_request:
    types: [opened, synchronize, reopened]

jobs:
  review:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
      - uses: your-org/superseded@main
        with:
          agent: claude-code
          model: claude-sonnet-4-6
          passes: security,correctness,performance,style,architecture
          post: true
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

### Action Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `agent` | No | `opencode` | AI CLI to use |
| `model` | No | — | Model ID |
| `passes` | No | — | Comma-separated passes (defaults to all enabled) |
| `post` | No | `false` | Post review as inline comments |

### Action Environment Variables

| Variable | Purpose |
|---|---|
| `GITHUB_TOKEN` | Auto-provided — used for `gh pr diff` and posting comments |
| `ANTHROPIC_API_KEY` | Required for `claude-code` agent |
| `OPENAI_API_KEY` | Required for `codex` agent |

The Action uses the `GITHUB_EVENT_PULL_REQUEST_NUMBER` environment variable (set by GitHub) to determine the PR to review.
