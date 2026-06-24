# Superseded

Multi-pass AI code review tool. Reviews PRs and diffs by delegating to AI CLIs (Claude Code, OpenCode, Codex) with focused prompts per review category.

## How It Works

Superseded runs **5 focused review passes in parallel**, each powered by an AI agent:

```
 PR or local diff
       │
       ▼
 ┌─────────────────────────────────────────────┐
 │  concurrent review passes (ThreadPool)      │
 │                                             │
 │  security      injection, auth, secrets, XSS│
 │  correctness   logic bugs, edge cases       │
 │  performance   N+1 queries, blocking I/O    │
 │  style         naming, dead code, complexity│
 │  architecture  coupling, API contracts      │
 └─────────────────────────────────────────────┘
       │
       ▼
 merge + deduplicate findings
       │
       ▼
 structured output (table / JSON / markdown)
       │
       ▼
 optional: post as GitHub PR review comments
```

Each pass sends a targeted prompt to your chosen agent (Claude Code, OpenCode, or Codex). The agent returns structured JSON findings — severity, file, line, description, fix suggestion — which get merged and deduplicated.

**Feedback loop**: dismiss a finding once and it won't appear in future reviews. Superseded tracks dismissed comments per-repo via a local SQLite store and injects them as negative context into subsequent runs.

## Install

```bash
git clone https://github.com/fabioesposito/superseded
cd superseded
uv sync
```

## Usage

### CLI

```bash
# Review a PR
superseded review --pr 123

# Review a local diff
superseded review --diff HEAD~3..HEAD

# Choose agent + model
superseded review --pr 123 --agent claude-code --model claude-sonnet-4-20250514

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
          model: claude-sonnet-4-20250514
          passes: security,correctness,performance
          post: true
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

### Server Mode

Run Superseded as a persistent service that receives GitHub App webhooks. Multiple repos install your app and get automatic reviews on every PR.

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

Or with a config file:

```bash
superseded serve --config /etc/superseded/server.yaml
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
  model: claude-sonnet-4-20250514
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

## Configuration

`.superseded.yaml` in repo root:

```yaml
agent: claude-code
model: claude-sonnet-4-20250514
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

## Supported Agents

| Agent | Invocation | Auth |
|-------|-----------|------|
| **claude-code** | `claude -p --bare --model` | `ANTHROPIC_API_KEY` |
| **opencode** | `opencode run` | Provider-specific |
| **codex** | `codex exec --json --model` | `CODEX_API_KEY` |

## Output Formats

### Table (default)
```
Sev          Pass           File                           Line   Title
---------------------------------------------------------------------------
🔴 critical  security       src/auth.py                    42     SQL injection
🟡 suggestion style         src/utils.py                   15     Unclear naming

Total: 2 findings
  critical: 1
  suggestion: 1
```

### JSON
```json
[
  {
    "severity": "critical",
    "pass_name": "security",
    "file": "src/auth.py",
    "line": 42,
    "end_line": 45,
    "title": "SQL injection in user query",
    "description": "User input interpolated into SQL string",
    "suggestion": "Use parameterized queries"
  }
]
```

### Markdown
Grouped by severity with inline code blocks and suggestions.

## Requirements

- Python 3.14+
- At least one AI CLI installed (claude-code, opencode, or codex)
- `gh` CLI for GitHub PR interaction

## Tests

```bash
uv run pytest tests/ -v
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

## License

MIT
