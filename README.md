# Superseded

Reviews that supersede themselves. Runs 5 parallel passes (security, correctness, performance, style, architecture) with Claude, Codex, or OpenCode. Posts findings as PR comments. Gets smarter every time you dismiss a finding.

## Quickstart (30 seconds)

```bash
git clone https://github.com/fabioesposito/superseded
cd superseded && uv sync && uv tool install .
superseded review --diff HEAD~1..HEAD
```

**Prerequisites:**
- Python 3.14+
- An AI CLI: `claude-code`, `opencode`, or `codex`
- GitHub CLI (`gh`) authenticated: `gh auth login`

## How It Works

```
PR or diff → 5 parallel passes → merge/deduplicate → structured output → optional PR comments
```

Each pass sends a targeted prompt to your chosen agent. Findings come back as structured JSON (severity, file, line, description, fix suggestion), merged and deduplicated.

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

# Choose agent + model
superseded review --pr 123 --agent claude-code --model claude-sonnet-4-6

# Output format
superseded review --pr 123 --format json
superseded review --pr 123 --format markdown
superseded review --pr 123 --format table  # default

# Post to GitHub PR
superseded review --pr 123 --post

# Selective passes
superseded review --pr 123 --passes security,correctness
```

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
          # SUPERSEDED_SERVER_URL: https://reviews.example.com
          # SUPERSEDED_SERVER_KEY: ${{ secrets.SUPERSEDED_SERVER_KEY }}
```

Set `server-url`/`server-key` (or the `SUPERSEDED_SERVER_URL`/`SUPERSEDED_SERVER_KEY` env vars) and install the App on the repo. The previous `agent`, `model`, `anthropic_api_key`, and `openai_api_key` inputs are removed — the server owns agent/model/credentials.

#### Using opencode with custom providers (DeepSeek, z.ai, ...)

The `opencode` agent delegates auth to opencode itself, which reads any provider
key from the environment. On the server, configure the provider key in the
server's environment — sandboxed runs pick it up from there (sbx via
`sbx secret set -g`, smolvm via the server env directly); for the local CLI,
add the key to your shell env and reference it from an `opencode.json`
committed at the repo root.

**1. Commit an `opencode.json`** using `{env:VAR}` substitution:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "deepseek": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "DeepSeek",
      "options": {
        "baseURL": "https://api.deepseek.com/v1",
        "apiKey": "{env:DEEPSEEK_API_KEY}"
      },
      "models": { "deepseek-chat": { "name": "DeepSeek Chat" } }
    },
    "zai": {
      "npm": "@ai-sdk/xai",
      "name": "z.ai",
      "options": { "apiKey": "{env:XAI_API_KEY}" },
      "models": { "glm-4.6": { "name": "GLM 4.6" } }
    }
  }
}
```

**2. Wire the secret into the workflow** and point superseded at the provider/model:

```yaml
- uses: fabioesposito/superseded@v1
  with:
    agent: opencode
    model: deepseek/deepseek-chat   # or zai/glm-4.6
    post: true
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
    DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
    # XAI_API_KEY: ${{ secrets.XAI_API_KEY }}
```

opencode reads env vars at runtime, so any provider your `opencode.json`
references via `{env:...}` just needs to be present in the step environment. See
the [opencode provider docs](https://opencode.ai/docs/providers) for the full
list of built-in providers and their SDK packages.

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
for the full Docker/compose guide and slim-image builds.

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
agent: null    # null = use each repo's .superseded.yaml
model: null    # null = use each repo's .superseded.yaml
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
| `SUPERSEDED_SERVER_AGENT` / `SUPERSEDED_SERVER_MODEL` | Override each repo's agent/model |
| `SUPERSEDED_SANDBOX` | `1`/`0` to run agents inside a sandbox microVM (default: `1` on the server) |
| `SUPERSEDED_SANDBOX_KIND` | `sbx` (default, Docker Sandboxes) or `smolvm` (smolmachines SDK) — see [Sandbox backends](#sandbox-backends) |
| `SUPERSEDED_SANDBOX_TIMEOUT` | Per-pass timeout inside the sandbox (seconds, default: `600`) |
| `SUPERSEDED_SANDBOX_KEEP_ON_ERROR` | `1` to leave the sandbox alive for inspection when a pass fails |
| `SUPERSEDED_SMOLVM_IMAGE_<AGENT>` | Per-agent OCI image for smolvm (e.g. `SUPERSEDED_SMOLVM_IMAGE_CLAUDE`); or `SUPERSEDED_SMOLVM_IMAGE` for one host-wide image |

### Sandbox backends

By default, server-mode reviews run each agent pass inside an ephemeral microVM.
Two backends are supported; pick one per deployment via `SUPERSEDED_SANDBOX_KIND`.

| Backend | Kind | Hosts | Credential flow |
|---|---|---|---|
| **Docker Sandboxes** (default) | `sbx` | Linux with KVM | `sbx secret set -g` injects via the host proxy; keys never enter the VM |
| **smolvm** (alternative) | `smolvm` | macOS (Hypervisor.framework), Linux (KVM), Windows (WHP) | `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` from the server's env, injected per-exec into the VM |

**sbx (default).** Install [`docker-sbx`](https://docs.docker.com/ai/sandboxes/), add the operator to the `kvm` group, run `sbx login`, and store provider keys once via `sbx secret set -g anthropic` / `-g openai`. Each review job creates one sandbox via `sbx create`; the 5 passes run as concurrent `sbx exec` calls into it; `sbx rm` tears it down.

**smolvm (alternative).** Use this on macOS, Windows, or Linux hosts where you want the embedded `smol` SDK instead of the `sbx` CLI. Install the extra and supply a per-agent OCI image whose `PATH` contains the agent CLI:

```bash
uv sync --extra sandbox
export SUPERSEDED_SANDBOX_KIND=smolvm
export SUPERSEDED_SMOLVM_IMAGE_CLAUDE=ghcr.io/your-org/superseded-claude:latest
# or one host-wide image for all three agents:
# export SUPERSEDED_SMOLVM_IMAGE=ghcr.io/your-org/superseded-all:latest
```

The server boots the OCI image as a microVM via the embedded `smol` SDK (libkrun,
in-process — no daemon, no CLI on `PATH`), mounts the per-job repo checkout at
`/workspace`, runs the 5 passes as concurrent `m.exec()` calls against the same
machine, then deletes it. Provider keys live only in the server's process
environment and are forwarded into each exec call as guest env — they never
persist on disk inside the VM. Network egress is open by default; lock it down
per agent by building the image with the agent's own egress controls.

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
- **Pluggable agents** — Use Claude Code, OpenCode, or Codex. Choose per-review or configure as default
- **Structured output** — JSON for piping, markdown for docs, terminal table for quick scanning
- **CI-native** — Docker-based GitHub Action. Runs on every PR, posts results as review comments
- **Server mode** — Self-hosted GitHub App. Multiple repos, webhook-driven, configurable concurrency
- **Sandbox isolation** — Every server-mode review runs inside an ephemeral microVM (Docker Sandboxes or smolvm). One VM per PR, destroyed when the review finishes
- **Static analysis pre-pass** — Auto-detects linters (ruff, mypy, eslint, bandit, gitleaks, go vet) and injects deterministic signals before AI review
- **Cross-file usage retrieval** — Extracts symbols from changed code, uses ripgrep to find callers across the repo
- **Reasoning trail** — Each finding includes agent rationale. Collapsible details in markdown and PR comments

## Supported Agents

| Agent | Invocation | Auth |
|-------|-----------|------|
| **claude-code** | `claude -p --bare --model` | `ANTHROPIC_API_KEY` |
| **opencode** | `opencode run` | Provider-specific |
| **codex** | `codex exec --json --model` | `CODEX_API_KEY` |

## Configuration

`.superseded.yaml` in repo root:

```yaml
agent: claude-code
model: claude-sonnet-4-6
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

Selection precedence for `agent`/`model`: **env vars > CLI flags > config**:

- `SUPERSEDED_AGENT` / `SUPERSEDED_MODEL` — override config and CLI flags. Set these as GitHub Action secrets to configure the container without commit-side edits.

## Requirements

- Python 3.14+
- **An AI CLI agent** (at least one required):
  | Agent | Install | Auth |
  |-------|---------|------|
  | **claude-code** | `npm install -g @anthropic-ai/claude-code` | `ANTHROPIC_API_KEY` |
  | **opencode** | `curl -fsSL https://opencode.ai/install.sh \| sh` | Provider-specific |
  | **codex** | `pip install openai-codex` | `CODEX_API_KEY` |
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
