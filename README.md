# Superseded

Local-first agentic pipeline harness. Write a ticket in markdown, and Superseded delegates implementation, testing, and release to AI agents running on your machine.

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
  - [Requirements](#requirements)
- [Configuration](#configuration)
- [Project Rules](#project-rules)
- [Multi-Repo Support](#multi-repo-support)
- [Per-Stage Agent Selection](#per-stage-agent-selection)
- [Ticket Format](#ticket-format)
- [Architecture](#architecture)
  - [Data Flow](#data-flow)
  - [Stage → Skill Mapping](#stage--skill-mapping)
- [Project Structure](#project-structure)
- [Running Tests](#running-tests)
- [Vendored Skills](#vendored-skills)
- [Key Design Decisions](#key-design-decisions)

## Features

- **Markdown tickets** — Issues live as `.md` files with YAML frontmatter in `.superseded/issues/`. Markdown is the single source of truth; SQLite is a fast cache/index.
- **Six-stage pipeline** — Spec → Plan → Build → Verify → Review → Ship, each powered by vendored agent skills.
- **Agent harness with retry loops** — Stages retry on failure with error context injected into re-prompts. Configurable `max_retries` per stage type.
- **Progressive context assembly** — Agents receive context in layers: AGENTS.md → docs/ index → ticket → previous artifacts → project rules → skill prompt → error context.
- **Execution plans** — The Plan stage writes a structured `plan.md` consumed by Build/Verify/Review stages.
- **Worktree isolation** — Build, Verify, and Review stages run in isolated git worktrees. Changes merge on success, discard on failure.
- **Quality enforcement** — Critical/important review findings loop back to Build. Project rules in `.superseded/rules.md` are injected into every prompt.
- **Web UI** — FastAPI + HTMX + Alpine.js + Tailwind CSS dashboard for ticket management, pipeline visualization, and iteration history.
- **Real-time updates** — Server-Sent Events push pipeline progress to the browser without polling.
- **Three agent adapters** — Claude Code, OpenCode, and Codex, all run as local CLI subprocesses.
- **Per-stage agent selection** — Configure different CLIs and models for each pipeline stage via config or UI.
- **Session history & observability** — Track agent events, pipeline iterations, and view live logs via SSE streaming.
- **GitHub integration** — Import issues from GitHub repositories.
- **CSRF protection & optional auth** — Built-in CSRF tokens for forms; optional API key authentication.

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
- `gh` CLI for Ship stage (PR creation)
- [Playwright](https://playwright.dev/) for browser UI testing (`npx playwright test`)

## Configuration

Superseded reads `.superseded/config.yaml` in the target repository:

```yaml
# Default agent and model (used when not overridden per-stage)
default_agent: opencode       # or "claude-code", "codex"
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

See [docs/multi-repo.md](docs/multi-repo.md) for multi-repo configuration details.

## Project Rules

Create `.superseded/rules.md` to define non-negotiable project rules. These rules are injected into every agent prompt:

```markdown
# Project Rules

- Run the full test suite before committing
- Write tests for every new feature
- Keep functions under 30 lines
- Use type hints on all function signatures
- Never commit secrets or credentials
```

## Multi-Repo Support

Tickets can target multiple repositories. Set `repos: [frontend, backend]` in ticket frontmatter to fan out BUILD/VERIFY/REVIEW stages across repos. SPEC and PLAN run once (primary repo), while SHIP creates a PR per target repo.

```yaml
# Ticket targeting multiple repos
---
id: SUP-001
title: Add user profile page
repos:
  - frontend
  - backend
---

Implement API endpoint and UI page for user profiles.
```

See [docs/multi-repo.md](docs/multi-repo.md) for full documentation.

## Per-Stage Agent Selection

Choose which CLI (claude-code, opencode, codex) and model to use for each pipeline stage. Configure via `.superseded/config.yaml` or the `/settings` UI.

```yaml
stages:
  spec:
    cli: claude-code
    model: claude-sonnet-4-20250514
  build:
    cli: opencode
    model: gpt-4o
  verify:
    cli: opencode
    model: gpt-4o
```

Stages without explicit configuration fall back to `default_agent` and `default_model`.

## Ticket Format

Tickets are markdown files with YAML frontmatter in `.superseded/issues/`:

```markdown
---
id: SUP-001
title: Add health check endpoint
status: new
stage: spec
labels:
  - feature
---

Create a /health endpoint that returns 200 OK with uptime info.
```

See [docs/tickets.md](docs/tickets.md) for the complete ticket format specification.

## Architecture

```
Browser (HTMX + Alpine.js + Jinja2)
  │ HTTP / SSE
FastAPI (single process)
  ├── Routes
  │   ├── /              Dashboard — list all issues
  │   ├── /issues/       Issue CRUD — create, view, detail
  │   ├── /pipeline/     Pipeline control — advance, retry, SSE events
  │   ├── /settings/     Configuration — repos, per-stage agents
  │   ├── /health        Health check endpoint
  │   └── /metrics       Pipeline metrics (redirects to /pipeline/metrics)
  ├── Pipeline Engine
  │   ├── ContextAssembler — 7-layer progressive context builder
  │   ├── HarnessRunner    — retry loop with error context injection
  │   ├── WorktreeManager  — git worktree lifecycle per issue
  │   └── Plan Parser       — read/write structured execution plans
  ├── Agent Runner
  │   ├── ClaudeCodeAdapter — spawns `claude` CLI
  │   ├── OpenCodeAdapter   — spawns `opencode` CLI
  │   └── CodexAdapter      — spawns `codex` CLI
  ├── SQLite (state cache)
  └── .superseded/
      ├── config.yaml
      ├── rules.md        — non-negotiable project rules
      ├── issues/         — markdown tickets (canonical)
      ├── artifacts/      — stage outputs (spec.md, plan.md, etc.)
      ├── worktrees/      — isolated git worktrees per issue
      └── state.db        — pipeline state (cache/index)
```

### Data Flow

1. User creates a ticket (markdown + YAML frontmatter in `.superseded/issues/`)
2. Advance triggers the current pipeline stage via `HarnessRunner`
3. `ContextAssembler` builds a 7-layer prompt: AGENTS.md → docs index → ticket → previous artifacts → project rules → skill prompt → retry errors
4. If the stage is Build/Verify/Review, a git worktree is created for isolation
5. The agent runs as a CLI subprocess in the worktree (or repo root for Spec/Plan/Ship)
6. On failure, the harness retries up to `max_retries`, injecting previous errors into the prompt
7. Results and iteration history are persisted to SQLite and displayed in the web UI
8. On success, the issue advances to the next stage; worktrees are cleaned up after Ship

### Stage → Skill Mapping

| Stage | Vendored Skill | Purpose |
|-------|---------------|---------|
| Spec | `spec-driven-development` | Generate detailed spec from ticket |
| Plan | `planning-and-task-breakdown` | Break spec into implementable tasks |
| Build | `incremental-implementation` | Implement code changes |
| Verify | `test-driven-development` | Run tests, fix failures |
| Review | `code-review-and-quality` | Review code quality and security |
| Ship | `git-workflow-and-versioning` | Commit, push, create PR via `gh` |

Each stage loads its skill from `vendor/agent-skills/skills/` when available, falling back to built-in prompts.

## Project Structure

```
src/superseded/
  main.py                  # FastAPI app factory and CLI entry point
  config.py                # YAML config loader (SupersededConfig)
  models.py                # Pydantic models (Issue, Stage, AgentResult, HarnessIteration)
  db.py                    # SQLite async operations (aiosqlite)
  tickets/
    reader.py              # Parse markdown issues with frontmatter
    writer.py              # Write/update issue files
  agents/
    base.py                # AgentAdapter protocol
    claude_code.py         # Claude Code CLI adapter
    opencode.py            # OpenCode CLI adapter
    codex.py               # Codex CLI adapter
    factory.py             # AgentFactory for per-stage agent selection
  pipeline/
    engine.py              # PipelineEngine — single-stage execution
    harness.py             # HarnessRunner — retry loop with context
    context.py             # ContextAssembler — 7-layer progressive context
    worktree.py            # WorktreeManager — git worktree lifecycle
    plan.py                # Plan parser — read/write structured plans
    prompts.py             # Stage prompts + skill loader
    stages.py              # Stage definitions and skill mapping
  routes/
    dashboard.py           # Dashboard view
    issues.py              # Issue CRUD routes
    pipeline.py            # Pipeline control routes + SSE
    settings.py            # Settings UI for agent configuration

templates/                 # Jinja2 + HTMX templates
tests/                     # pytest test suite (230+ tests)

.superseded/
  config.yaml              # Project configuration
  rules.md                 # Non-negotiable project rules
  issues/                  # Ticket files (canonical source of truth)
  artifacts/               # Stage output artifacts
  state.db                 # SQLite state cache

vendor/
  agent-skills/            # addyosmani/agent-skills (pipeline stage skills)
  impeccable/              # pbakaus/impeccable (UI design skills)
```

## Running Tests

```bash
# Run all tests
uv run pytest tests/ -v

# Run specific test modules
uv run pytest tests/test_harness.py -v
uv run pytest tests/test_context.py -v
uv run pytest tests/test_worktree.py -v

# Run browser UI tests
npx playwright test
```

## Vendored Skills

### Agent Skills (addyosmani/agent-skills)

Located at `vendor/agent-skills/skills/`. 20 production-grade engineering skills powering pipeline stages:

- **Pipeline stages** → `spec-driven-development`, `planning-and-task-breakdown`, `incremental-implementation`, `test-driven-development`, `code-review-and-quality`, `git-workflow-and-versioning`
- **Other useful skills** → `api-and-interface-design`, `debugging-and-error-recovery`, `security-and-hardening`, `performance-optimization`

### Impeccable (pbakaus/impeccable)

Located at `vendor/impeccable/source/skills/`. Design skill with 18 commands for UI work:

- `/impeccable craft` — full build flow
- `/audit` before UI changes
- `/polish` as a final pass
- `/critique` for UX design reviews

## Key Design Decisions

- **Monolith** — single process, single `uv run superseded`
- **Local agents only** — agents run on your machine as CLI subprocesses, no containers or cloud
- **In-repo `.superseded/`** — tickets, artifacts, and state live in the repository
- **SQLite as cache** — markdown is canonical, SQLite is a fast index
- **SSE for real-time** — pipeline progress pushed to browser without polling
- **Personal single-user** — no auth, no multi-tenant, no hosting