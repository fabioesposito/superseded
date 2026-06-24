# Superseded — Agent Configuration

## Project

Superseded is a local-first agentic pipeline tool. You write tickets (markdown specs), the pipeline delegates implementation, testing, and release to AI agents.

## Tech Stack

- Python 3.14+ with `uv` for dependency management
- FastAPI + HTMX + Alpine.js + Tailwind CSS (CDN) for the web UI
- SQLite (aiosqlite) for pipeline state
- Jinja2 for templating
- Agents run as CLI subprocesses (Claude Code, OpenCode)

## Commands

```bash
uv run pytest tests/ -v          # Run all tests
uv run superseded                  # Start the server
uv sync                            # Install dependencies
uv run ruff check src/ tests/     # Lint
uv run ruff format src/ tests/    # Format
uv run ruff check --fix src/ tests/  # Lint + auto-fix
npx playwright test                # Run Playwright browser tests (UI validation)
```

## Tool Requirements

- **GitHub interactions**: Always use `gh` CLI for PRs, issues, and repo operations. `gh pr create`, `gh pr merge`, `gh issue list`, etc. Do not use raw git push + manual GitHub URLs.
- **UI test validation**: Use `npx playwright` to verify HTMX interactions, pipeline progress rendering, and SSE updates in a real browser. Run `npx playwright test` to execute browser-based tests or `npx playwright codegen` to generate test scripts interactively. Tests target `http://localhost:8000/` — ensure the server is running before executing Playwright tests.
- **Context7 for planning**: Use the Context7 MCP tools (`context7_resolve-library-id` + `context7_query-docs`) when planning tasks that involve external libraries. Query docs for FastAPI, HTMX, Jinja2, or any dependency before implementing — the code snippets and API references are authoritative and up-to-date.

## Architecture

- `src/superseded/models.py` — Pydantic models (Issue, Stage, AgentResult)
- `src/superseded/config.py` — YAML config loader
- `src/superseded/tickets/` — Markdown + YAML frontmatter CRUD for issues
- `src/superseded/db.py` — SQLite async operations
- `src/superseded/agents/` — Agent adapters (Claude Code, OpenCode)
- `src/superseded/pipeline/` — Pipeline engine, stage definitions, prompts
- `src/superseded/harness/` — Harness class, checkpoint, context, lifecycle, verification
- `src/superseded/notifications.py` — NotificationService (ntfy.sh, Slack, webhook)
- `src/superseded/cli.py` — `superseded init` command
- `src/superseded/validation.py` — Input validation
- `src/superseded/routes/` — FastAPI route handlers
- `templates/` — Jinja2 + HTMX templates

## Skills

This project vendors two skill repositories:

### Agent Skills (addyosmani/agent-skills)

Located at `vendor/agent-skills/skills/`. 20 production-grade engineering skills:

- **Pipeline stages map to skills:**
  - Spec → `spec-driven-development`
  - Plan → `planning-and-task-breakdown`
  - Build → `incremental-implementation`
  - Verify → `test-driven-development`
  - Review → `code-review-and-quality`
  - Ship → `git-workflow-and-versioning`

- **Other useful skills for this project:**
  - `api-and-interface-design` — when designing the web API
  - `debugging-and-error-recovery` — when fixing bugs
  - `security-and-hardening` — before shipping
  - `performance-optimization` — when profiling

### Impeccable (pbakaus/impeccable)

Located at `vendor/impeccable/source/skills/`. Design skill with 18 commands:

- **When working on UI (templates, HTMX, styling):**
  - Start with `/impeccable craft` for the full build flow
  - Use `/audit` before making UI changes
  - Use `/polish` as a final pass before shipping
  - Use `/critique` for UX design reviews
  - Use `/layout` and `/typeset` for spacing/typography fixes
  - Use `/colorize` and `/animate` for strategic color and motion
  - Use `/harden` for error handling, onboarding, and edge cases

- **Key anti-patterns to avoid:**
  - No Inter font, no purple gradients, no card-nesting
  - No gray text on colored backgrounds
  - No pure black/gray (always tint)
  - No bounce/elastic easing

## Conventions

- Tickets are markdown files with YAML frontmatter in `.superseded/issues/`
- SQLite is a cache/index — markdown is the canonical source of truth
- Pipeline stages flow: Spec → Plan → Build → Verify → Review → Ship
- Templates use HTMX for partial updates and Alpine.js for interactivity
- All Python uses `from __future__ import annotations`
- No comments in code unless explicitly requested

## Harness Features

Superseded is now an agent harness, not just a linear pipeline:

- **Feedback loops**: Stages retry on failure with error context injected into re-prompts. Auto-retry configurable for transient failures (`auto_retry: true` in config). Retry is also manual — click Retry in the UI.
- **Execution plans**: The Plan stage writes structured `plan.md` to `.superseded/artifacts/{id}/plan.md`. Build/Verify/Review stages consume it. Plans track task status (pending/in-progress/complete) with progress injected into prompts.
- **Progressive context**: Agents receive context in layers: AGENTS.md → docs/ index → ticket → previous artifacts → rules → skill prompt → error context. Token-aware with adaptive sizing — drops low-priority layers when over budget.
- **Worktree isolation**: BUILD/VERIFY/REVIEW stages run in isolated git worktrees. Changes merge on success via `--no-ff`, discard on failure.
- **Quality enforcement**: Review findings that are critical/important loop back to BUILD. `.superseded/rules.md` is injected into every prompt. Output quality gates enforce code patterns in BUILD and test results in VERIFY.
- **Structured verification feedback**: Failures grouped by type (missing sections, test failures, review findings) for faster agent comprehension. Not a flat error dump.
- **Cross-stage quality signals**: Verified stages inject quality context into downstream prompts ("SPEC was verified — focus on implementation accuracy").
- **Curated error context**: Duplicate errors deduplicated and sorted by frequency. Agents see distinct, prioritized errors only.
- **Selective docs loading**: Docs index filtered by stage relevance (BUILD gets architecture+guides, SHIP gets guides+operations).
- **Iteration history**: Every harness attempt is tracked in the database and shown in the UI.
- **Multi-repo support**: Tickets can target multiple repositories. Set `repos: [frontend, backend]` in ticket frontmatter. Available repos are defined in `.superseded/config.yaml` under the `repos` key. SPEC/PLAN run once (primary repo). BUILD/VERIFY/REVIEW fan out per target repo. SHIP creates a PR per repo. See `docs/architecture/multi-repo.md`.
- **Verification engine**: Validates stage outputs — artifact section validation, review severity parsing, test result parsing. Configurable per stage.
- **Health monitoring**: `/health` endpoint reports status, running issues, and active stages. Silent agents (>5 min no output) flagged in logs.
- **Checkpoints and crash recovery**: Stage progress saved to `.superseded/checkpoints/` during execution. Server resumes from last checkpoint on restart. Checkpoints cleared on stage success.
- **Notifications**: Push notifications via ntfy.sh, Slack webhooks, and generic HTTP webhooks on stage completion, failure, and approval requests.
- **Docker sandboxing**: Run agents in isolated Docker containers with configurable resource limits (2GB memory, 2 CPUs, 256 PIDs default).
- **Resource limits**: Per-stage caps on max tokens, wall time, and cost. Exceeded limits fail the stage with a clear error. Enforced during streaming execution.
- **File-level review approval**: Individual changed files can be approved or rejected during Review. All files must be approved before advancing.
- **Bulk retry**: Select multiple paused issues on the dashboard and retry them all at once.
- **Auto-advance**: Skip manual stage transitions when verification passes. Approval-requiring stages still pause for human input.
- **Graceful shutdown**: `/health` reports `shutting-down` status. Running stages complete before the process exits.

## RTK Integration

Superseded can optionally integrate with [RTK](https://github.com/rtk-ai/rtk) (Rust Token Killer) to reduce token burn during pipeline stages. RTK is a CLI proxy that filters and compresses verbose command output (e.g., `git status`, `pytest`, `cargo test`) before it reaches the LLM context window.

RTK does **not** replace Claude Code, OpenCode, or Codex — it hooks into their Bash tool calls to save 60–90% tokens.

### Enabling RTK

Add `rtk: true` to `.superseded/config.yaml` globally or per-stage:

```yaml
rtk: true
stages:
  build:
    cli: claude-code
    model: claude-sonnet-4-20250514
    rtk: true
  verify:
    cli: opencode
    rtk: false
```

The `rtk` binary must be available in `$PATH`. The harness automatically runs `rtk init -g --<agent>` before spawning the agent. For Docker sandboxes, RTK is installed and initialized inside the ephemeral container.

## Key Files for Agents

- `.superseded/issues/` — Tickets (markdown + YAML frontmatter), single source of truth. See `docs/guides/tickets.md` for format.
- `.superseded/artifacts/{id}/` — Stage outputs (spec.md, plan.md, etc.)
- `.superseded/rules.md` — Non-negotiable project rules injected into every prompt
- `.superseded/config.yaml` — Harness configuration
- `.superseded/state.db` — Pipeline state cache (markdown is canonical)
- `docs/` — Structured project documentation (indexed by ContextAssembler):
  - `docs/architecture/` — System design, component diagrams, data flow
  - `docs/guides/` — How-to docs (user guide, ticket format)
  - `docs/adrs/` — Architectural Decision Records (dated design/plan docs)
  - `docs/operations/` — Runbooks, setup, troubleshooting