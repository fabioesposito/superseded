# Superseded — Product Requirements

## Core Concept
Local-first agentic pipeline tool. You write the ticket, the pipeline does the rest.
- Tickets are markdown files in `.superseded/issues/` (single source of truth)
- Pipeline: Spec → Plan → Build → Verify → Review → Ship
- Agents: Claude Code and OpenCode, run as local CLI subprocesses
- Web UI: FastAPI + HTMX dashboard for ticket management, pipeline visualization, and review

## Tech Stack
Python 3.12+ / uv / FastAPI / HTMX / Alpine.js / Tailwind CDN / SQLite / Jinja2 / aiosqlite / python-frontmatter / pyyaml / pydantic / sse-starlette

## Architecture
Monolithic FastAPI + HTMX application. Single process serves web UI and runs the pipeline engine. Agent adapters spawn CLI tools as subprocesses. In-repo `.superseded/` directories hold tickets (markdown with YAML frontmatter) and pipeline state (SQLite). Markdown is canonical — SQLite is a cache/index.

```
Browser (HTMX + Alpine.js + Jinja2)
  │ HTTP / SSE
FastAPI (single process)
  ├── Routes (dashboard, issues, pipeline)
  ├── Pipeline Engine (6 stages)
  ├── Agent Runner
  │   ├── Claude Code adapter
  │   └── OpenCode adapter
  ├── SQLite (state cache)
  └── .superseded/
      ├── config.yaml
      ├── issues/ (markdown)
      ├── artifacts/ (stage outputs)
      └── state.db (pipeline state)
```

## Pipeline Stages (mapped to agent-skills)

| Stage | Agent Skill | Description |
|-------|------------|-------------|
| Spec | spec-driven-development | Generate detailed spec from ticket |
| Plan | planning-and-task-breakdown | Break spec into implementable tasks |
| Build | incremental-implementation | Implement code changes |
| Verify | test-driven-development | Run tests, fix failures |
| Review | code-review-and-quality | Review code quality and security |
| Ship | git-workflow-and-versioning | Commit, push, create PR |

Each stage loads its corresponding skill from `vendor/agent-skills/skills/` when available, falling back to built-in prompts.

## Vendored Skill Repos
- **addyosmani/agent-skills** → `vendor/agent-skills/` — Pipeline stage prompts and engineering practices
- **pbakaus/impeccable** → `vendor/impeccable/` — UI design skill with 18 commands (audit, polish, critique, etc.)

When working on UI (templates, HTMX, styling):
- Use `/impeccable craft` for the full build flow
- Use `/audit` before making UI changes
- Use `/polish` as a final pass before shipping
- Anti-patterns: no Inter font, no purple gradients, no card-nesting, no gray text on colored backgrounds

## Key Decisions
- **Monolith** — single process, single `uv run superseded`
- **Local agents only** — agents run on your machine as CLI subprocesses
- **In-repo `.superseded/`** — tickets, artifacts, and state live in the repo
- **SQLite as cache** — markdown is canonical, SQLite is a fast index
- **SSE for real-time** — pipeline progress pushed to browser via Server-Sent Events
- **Personal single-user** — no auth, no multi-tenant, no hosting

## Competitive Positioning
Unlike GitHub Copilot (IDE-focused), Replit (hosted), or Aider (CLI-only):
- Canonical per-issue markdown as single source of truth
- Agent pipelines that mirror the skill lifecycle (spec → ship)
- Web UI for visual pipeline control without leaving terminal
- Local-first: your code stays on your machine

## See Also
- `docs/plans/2026-04-11-superseded-design.md` — Full architecture design
- `docs/plans/2026-04-11-superseded-implementation.md` — Implementation plan