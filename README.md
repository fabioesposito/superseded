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
      - uses: actions/checkout@v4
      - uses: fabioesposito/superseded@v1
        with:
          agent: claude-code
          model: claude-sonnet-4-6
          passes: security,correctness,performance
          post: true
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

### Server Mode (Self-Hosted)

Run Superseded as a persistent GitHub App. Multiple repos install your app and get automatic reviews on every PR.

```bash
# Install server dependencies
uv pip install -e ".[server]"

# Set environment variables
export SUPERSEDED_APP_ID=12345
export SUPERSEDED_WEBHOOK_SECRET=whsec_...
export SUPERSEDED_PRIVATE_KEY_PATH=/path/to/private-key.pem

# Start the server
superseded serve --port 8000
```

**Server config file** (`/etc/superseded/server.yaml`):

```yaml
app_id: 12345
webhook_secret: whsec_...
private_key_path: /path/to/key.pem
max_concurrent_reviews: 3
temp_dir: /tmp/superseded
log_level: info

defaults:
  agent: claude-code
  model: claude-sonnet-4-6
  passes: [security, correctness, performance, style, architecture]
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
| `SUPERSEDED_PORT` | Server port (default: 8000) |
| `SUPERSEDED_HOST` | Server host (default: 0.0.0.0) |

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
