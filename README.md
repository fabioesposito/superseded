# Superseded

Local-first agentic pipeline harness. Write a ticket in markdown, and Superseded delegates implementation, testing, and release to AI agents running on your machine.

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
- **Two agent adapters** — Claude Code and OpenCode, both run as local CLI subprocesses.

## Quick Start

```bash
# Install dependencies
uv sync

# Start the server (defaults to http://127.0.0.1:8000)
uv run superseded

# Or specify a repo path and port
uv run superseded /path/to/my/project --port 3000
```

### Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for dependency management
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) or [OpenCode](https://github.com/opencodeco/opencode) CLI installed and available on `$PATH`
- `gh` CLI for Ship stage (PR creation)
- [Playwright](https://playwright.dev/) for browser UI testing (`npx playwright test`)

## Configuration

Superseded reads `.superseded/config.yaml` in the target repository:

```yaml
default_agent: claude-code    # or "opencode"
stage_timeout_seconds: 600
port: 8000
host: 127.0.0.1
max_retries: 3
retryable_stages:
  - build
  - verify
  - review
```

## Architecture

```
Browser (HTMX + Alpine.js + Jinja2)
  │ HTTP / SSE
FastAPI (single process)
  ├── Routes
  │   ├── /              Dashboard — list all issues
  │   ├── /issues/       Issue CRUD — create, view, detail
  │   └── /pipeline/    Pipeline control — advance, retry, SSE events
  ├── Pipeline Engine
  │   ├── ContextAssembler — 7-layer progressive context builder
  │   ├── HarnessRunner    — retry loop with error context injection
  │   ├── WorktreeManager  — git worktree lifecycle per issue
  │   └── Plan Parser       — read/write structured execution plans
  ├── Agent Runner
  │   ├── ClaudeCodeAdapter — spawns `claude` CLI
  │   └── OpenCodeAdapter   — spawns `opencode` CLI
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
    pipeline.py             # Pipeline control routes + SSE

templates/                 # Jinja2 + HTMX templates
tests/                     # pytest test suite (65 tests)

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