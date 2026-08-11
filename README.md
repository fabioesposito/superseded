# Superseded

Reviews that supersede themselves. Runs 5 parallel passes (security, correctness, performance, style, architecture) with the DeepSeek API. Posts findings as PR comments. Gets smarter every time you dismiss a finding.

## Setup (30 seconds)

```bash
git clone https://github.com/fabioesposito/superseded
cd superseded && uv sync && uv tool install .
```

1. Get a DeepSeek API key at <https://platform.deepseek.com>.
2. Set it: `export SUPERSEDED_DEEPSEEK_API_KEY=sk-...`
3. (Optional) Run `superseded init` to write a `.superseded.yaml`.
4. Review a PR: `superseded review --pr 123`

**Prerequisites:**
- Python 3.14+
- A DeepSeek API key (`SUPERSEDED_DEEPSEEK_API_KEY`)
- GitHub CLI (`gh`) authenticated: `gh auth login`

See `MIGRATION.md` if you're upgrading from v0.5.x.

## How It Works

```
PR or diff → 5 parallel passes → merge/deduplicate → structured output → optional PR comments
```

Each pass sends a targeted prompt to the provider. Findings come back as structured JSON (severity, file, line, description, fix suggestion), merged and deduplicated.

**Feedback loop:** Dismiss a finding once and it won't appear in future reviews. Superseded tracks dismissed comments per-repo via SQLite and injects them as negative context.

## Usage

### CLI

```bash
# Review a PR
superseded review --pr 123

# Review a local diff
superseded review --diff HEAD~3..HEAD

# Review a branch
superseded review --diff main..feature-branch

# Review uncommitted changes (no args = git diff HEAD; --staged = index only)
superseded review
superseded review --staged

# Structured JSON logs on stderr (also: --log-level INFO)
superseded --log-format json review --pr 123

# Choose provider + model
superseded review --pr 123 --provider deepseek --model deepseek-v4-flash

# Output format
superseded review --pr 123 --format json
superseded review --pr 123 --format markdown
superseded review --pr 123 --format table  # default

# Post to GitHub PR
superseded review --pr 123 --post

# Selective passes
superseded review --pr 123 --passes security,correctness

# Run/inspect database migrations explicitly (the tool also auto-migrates on startup)
superseded migrate
superseded migrate --database-url postgresql://user:pass@host/superseded   # prints the head revision
```

The memory database (`.superseded/memory.db`, SQLite) and the server's Postgres backend are managed by Alembic migrations. The schema is brought to the latest revision automatically every time a store opens — you don't normally need to do anything. `superseded migrate` exists for running or inspecting migrations deliberately (e.g. before a server deploy); it prints the resulting revision. Pre-existing databases from older versions are adopted transparently on first run (no manual step, no data loss).

### GitHub Action

The Action is a thin client: it POSTs the PR to a running Superseded server,
which runs the review via the DeepSeek API and posts the result via its GitHub
App. **Breaking:** the Action no longer builds a Docker image or runs the
agents itself — you must (a) install the Superseded GitHub App on the repo and
(b) run the server somewhere reachable. No `permissions:` block is needed on
the workflow (the server's App does all GitHub writes).

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

Set `server-url`/`server-key` (or the `SUPERSEDED_SERVER_URL`/`SUPERSEDED_SERVER_KEY` env vars) and install the App on the repo. The previous `agent`, `model`, `anthropic_api_key`, and `openai_api_key` inputs are removed — the server owns provider/model/credentials. The server must have `SUPERSEDED_DEEPSEEK_API_KEY` set in its environment; the Action does not take (and should not be given) the key.

### Server Mode (Self-Hosted)

Run Superseded as a persistent GitHub App. Multiple repos install your app and get automatic reviews on every PR.

**Run directly:**

```bash
# Dependencies (fastapi, uvicorn, asyncpg, ...) ship with the package.
uv sync

# Set environment variables
export SUPERSEDED_APP_ID=12345
export SUPERSEDED_WEBHOOK_SECRET=whsec_...
export SUPERSEDED_PRIVATE_KEY_PATH=/path/to/private-key.pem
export SUPERSEDED_DEEPSEEK_API_KEY=sk-...

# Start the server
superseded serve --port 8000
```

**Run with Docker Compose** (server + Postgres, secrets from a gitignored `.env`):

```bash
cp .env.example .env                       # fill in the required values
mkdir -p keys && cp /path/to/private-key.pem keys/private-key.pem
docker compose up -d                       # api + postgres
```

The API binds `0.0.0.0:8000` **inside the compose network only**; terminate TLS at
a reverse proxy in front of compose. `SUPERSEDED_BEHIND_PROXY=1` (set by compose)
tells the server TLS terminates upstream. See [Server Mode](docs/superseded/server.md)
for the full server guide.

**Server config file** (`/etc/superseded/server.yaml`) — alternatively to env vars:

```yaml
app_id: 12345
webhook_secret: whsec_...
private_key_path: /etc/superseded/github-app.pem
host: 127.0.0.1
port: 8000
max_concurrent_reviews: 3
temp_dir: /tmp/superseded
log_level: info
provider: deepseek      # provider is fixed server-side
model: null             # null = use each repo's .superseded.yaml
deepseek_api_key: sk-...   # or set SUPERSEDED_DEEPSEEK_API_KEY
```

**GitHub App setup:**

1. Create a GitHub App at https://github.com/settings/apps
2. Set webhook URL to `https://your-server.com/webhook`
3. Subscribe to `pull_request` and `installation` events
4. Grant permissions: `pull_requests: write`, `checks: write`, `contents: read`
5. Install the app on your repos

**Environment variables:**

| Variable | Purpose |
|----------|---------|
| `SUPERSEDED_APP_ID` | GitHub App ID |
| `SUPERSEDED_PRIVATE_KEY_PATH` | Path to private key PEM |
| `SUPERSEDED_WEBHOOK_SECRET` | Webhook signature secret |
| `SUPERSEDED_MAX_CONCURRENT` | Max parallel reviews (default: 3) |
| `SUPERSEDED_PORT` / `SUPERSEDED_HOST` | Bind address (default: `127.0.0.1:8000`) |
| `SUPERSEDED_BEHIND_PROXY` | Set `1` when TLS terminates at an upstream reverse proxy (allows binding `0.0.0.0` without in-process TLS) |
| `SUPERSEDED_DATABASE_URL` | `postgresql://...` for Postgres; omit for SQLite |
| `SUPERSEDED_SERVER_MODEL` | Override each repo's default model |
| `SUPERSEDED_DEEPSEEK_API_KEY` | DeepSeek API key (required — the server refuses to start without it) |

On startup, `superseded serve` runs pending Alembic migrations against the configured database (SQLite or Postgres) before accepting requests. To run or inspect migrations ahead of a deploy instead, run `superseded migrate --database-url ...` (honors `SUPERSEDED_DATABASE_URL`). Pre-existing databases are auto-adopted on first run.

### Feedback

```bash
# Review a PR and post findings (maps them for later feedback)
superseded review --pr 123 --post

# Pull reactions/resolutions on past comments and update memory
superseded feedback --check --pr 123

# Manually mark a finding (by its GitHub comment id)
superseded feedback <comment-id> --dismiss
superseded feedback <comment-id> --helpful
```

Dismissed findings are injected into future review prompts so the tool avoids repeating rejected comments.

## Features

- **Multi-pass review** — 5 specialized passes with focused prompts (security, correctness, performance, style, architecture)
- **Feedback memory** — SQLite store tracks dismissed findings and their reasoning. Future reviews learn from your team's decisions
- **GitHub integration** — Post findings as inline PR review comments. Critical issues request changes, suggestions post as comments
- **Pluggable providers** — Direct DeepSeek API calls. Choose per-review or configure as default
- **Structured output** — JSON for piping, markdown for docs, terminal table for quick scanning
- **CI-native** — Composite GitHub Action hands each PR to your review server; no agents or secrets in CI
- **Server mode** — Self-hosted GitHub App. Multiple repos, webhook-driven, configurable concurrency
- **Static analysis pre-pass** — Auto-detects linters (ruff, mypy, eslint, bandit, gitleaks, go vet) and injects deterministic signals before AI review
- **Cross-file usage retrieval** — Extracts symbols from changed code, uses ripgrep to find callers across the repo
- **Reasoning trail** — Each finding includes agent rationale. Collapsible details in markdown and PR comments

## Supported Provider

| Provider | Auth |
|----------|------|
| **deepseek** | `SUPERSEDED_DEEPSEEK_API_KEY` |

## Configuration

`.superseded.yaml` in repo root:

```yaml
provider: deepseek
model: deepseek-v4-flash
passes:
  security: true
  correctness: true
  performance: true
  style: true
  architecture: true
post_to_pr: false
format: table
memory: true
```

Selection precedence for `provider`/`model`: **env vars > CLI flags > config**:

- `SUPERSEDED_PROVIDER` / `SUPERSEDED_MODEL` — override config and CLI flags. (`SUPERSEDED_AGENT` still works as a deprecated alias for `SUPERSEDED_PROVIDER`.)

## Requirements

- Python 3.14+
- **A DeepSeek API key** (required): `SUPERSEDED_DEEPSEEK_API_KEY` — get one at <https://platform.deepseek.com>
- **git** (required) — comes standard on most systems
- **GitHub CLI (`gh`)** (required for `--pr` and `--post`): `gh auth login` must be authenticated
- **gitleaks** (optional) — static analysis for secrets/hardcoded keys; runs automatically when on PATH. Install: `brew install gitleaks` or https://github.com/gitleaks/gitleaks

## Tests

```bash
uv run pytest tests/ -v
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

## License

MIT
