# Superseded

Local-first agentic pipeline harness. Write a ticket in markdown, and Superseded delegates implementation, testing, and release to AI agents running on your machine.

## Features

- **Markdown tickets** — Issues live as `.md` files with YAML frontmatter in `.superseded/issues/`
- **Six-stage pipeline** — Spec → Plan → Build → Verify → Review → Ship
- **Agent harness with retry loops** — Stages retry on failure with error context injected into re-prompts
- **Progressive context assembly** — Agents receive context in layers: AGENTS.md → docs/ index → ticket → previous artifacts → project rules → skill prompt
- **Worktree isolation** — Build/Verify/Review stages run in isolated git worktrees
- **Web UI** — FastAPI + HTMX + Alpine.js + Tailwind CSS dashboard with real-time SSE updates
- **Three agent adapters** — Claude Code, OpenCode, and Codex, all run as local CLI subprocesses
- **Per-stage agent selection** — Configure different CLIs and models per pipeline stage
- **Multi-repo support** — Tickets can target multiple repositories
- **GitHub integration** — Import issues from GitHub repositories

## Quick Start

```bash
# Install dependencies
uv sync

# Start the server (defaults to http://127.0.0.1:8000)
uv run superseded

# Or specify a repo path and port
uv run superseded /path/to/my/project --port 3000

# Or specify host and port
uv run superseded --host 0.0.0.0 --port 8000
```

### Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for dependency management
- At least one of: [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [OpenCode](https://github.com/opencodeco/opencode), or [Codex](https://github.com/openai/codex) CLI installed and available on `$PATH`
- `gh` CLI for the Ship stage (PR creation)
- [Playwright](https://playwright.dev/) for browser UI testing (`npx playwright test`)

## Configuration

Superseded reads `.superseded/config.yaml` in the target repository:

```yaml
# Default agent and model
default_agent: opencode
default_model: opencode-go/kimi-k2.5

# Server settings
port: 8000
host: 0.0.0.0

# Pipeline settings
stage_timeout_seconds: 600
max_retries: 3
retryable_stages:
  - build
  - verify
  - review

# Multi-repo support (optional)
repos:
  frontend:
    path: /home/user/my-frontend
  backend:
    path: /home/user/my-backend

# Per-stage agent selection (optional)
stages:
  spec:
    cli: claude-code
    model: claude-sonnet-4-20250514
  build:
    cli: opencode
    model: gpt-4o
```

## Running Tests

```bash
uv run pytest tests/ -v            # Run all tests
uv run pytest tests/test_harness.py # Specific module
npx playwright test                  # Browser UI tests

# Lint and format
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

## Documentation

- **[User Guide](docs/user-guide.md)** — Complete usage documentation: creating tickets, pipeline stages, agent configuration, multi-repo support, settings, and troubleshooting
- **[Ticket Format](docs/tickets.md)** — Detailed ticket specification
- **[Multi-Repo Support](docs/multi-repo.md)** — Multi-repo configuration and behavior

## Project Structure

```
src/superseded/
  main.py            # FastAPI app factory and CLI entry point
  config.py          # YAML config loader
  models.py          # Pydantic models
  db.py              # SQLite async operations
  tickets/            # Markdown + frontmatter CRUD
  agents/             # Agent adapters (Claude Code, OpenCode, Codex)
  pipeline/           # Pipeline engine, context assembler, harness, worktrees
  routes/             # FastAPI route handlers

templates/            # Jinja2 + HTMX templates
tests/                # pytest suite
.superseded/          # Runtime data (config, tickets, artifacts, state.db)
vendor/               # Vendored skill repositories
```

## Key Design Decisions

- **Monolith** — single process, single `uv run superseded`
- **Local agents only** — agents run as CLI subprocesses on your machine
- **In-repo `.superseded/`** — tickets, artifacts, and state live in the repository
- **SQLite as cache** — markdown is the single source of truth; SQLite is a fast index
- **SSE for real-time** — pipeline progress pushed to the browser without polling