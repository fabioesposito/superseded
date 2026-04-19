# Superseded — Agent Configuration

## Project

Superseded is a local-first agentic pipeline tool. You write tickets (markdown specs), the pipeline delegates implementation, testing, and release to AI agents.

## Tech Stack

- Python 3.12+ with `uv` for dependency management
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

- **Feedback loops**: Stages retry on failure with error context injected into re-prompts. Configurable via `max_retries` in `.superseded/config.yaml`.
- **Execution plans**: The Plan stage writes structured `plan.md` to `.superseded/artifacts/{id}/plan.md`. Build/Verify/Review stages consume it.
- **Progressive context**: Agents receive context in layers: AGENTS.md → docs/ index → ticket → previous artifacts → rules → skill prompt → error context.
- **Worktree isolation**: BUILD/VERIFY/REVIEW stages run in isolated git worktrees. Changes merge on success, discard on failure.
- **Quality enforcement**: Review findings that are critical/important loop back to BUILD. `.superseded/rules.md` is injected into every prompt.
- **Iteration history**: Every harness attempt is tracked in the database and shown in the UI.
- **Multi-repo support**: Tickets can target multiple repositories. Set `repos: [frontend, backend]` in ticket frontmatter. Available repos are defined in `.superseded/config.yaml` under the `repos` key. SPEC/PLAN run once (primary repo). BUILD/VERIFY/REVIEW fan out per target repo. SHIP creates a PR per repo. See `docs/architecture/multi-repo.md`.

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