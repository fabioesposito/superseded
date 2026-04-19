---
title: Superseded Design Document
category: adrs
summary: Superseded Design Document
tags: []
date: 2026-04-11
---

# Superseded Design Document

## Goal

Build a local-first agentic pipeline tool where a solo engineer writes tickets (markdown specs) and delegates implementation, testing, and release to an automated pipeline powered by Claude Code and OpenCode.

## Architecture

Monolithic FastAPI + HTMX application. Single process serves web UI and runs the pipeline engine. Agent adapters spawn CLI tools as subprocesses. In-repo `.superseded/` directories hold tickets (markdown) and pipeline state (SQLite). The markdown file is the single source of truth.

## Key Decisions

- **Monolith** (not microservices) — simplest for a personal tool, one `uv run superseded`
- **Local agents** — Claude Code and OpenCode run as CLI subprocesses on your machine
- **In-repo markdown** — tickets live in `.superseded/issues/` with YAML frontmatter, git-trackable
- **SQLite for state** — pipeline progress, agent logs, stage results; markdown is canonical, SQLite is a cache
- **HTMX + Alpine.js** — no JS build step, server-rendered with progressive enhancement
- **SSE for real-time** — pipeline progress pushed to browser via Server-Sent Events

## Architecture Diagram

```
┌─────────────────────────────────────────────────┐
│                  User's Browser                  │
│            HTMX + Alpine.js + Jinja2            │
└──────────────────────┬──────────────────────────┘
                       │ HTTP / SSE
┌──────────────────────▼──────────────────────────┐
│              FastAPI (single process)            │
│  ┌─────────┐ ┌──────────┐ ┌──────────────────┐│
│  │ Routes  │ │ Pipeline  │ │   Agent Runner    ││
│  │(tickets │ │  Engine   │ │ ┌───────┐┌───────┐││
│  │  CRUD,  │ │(5 stages) │ │ │Claude ││Open-  │││
│  │ pipeline│ │           │ │ │Code   ││Code   │││
│  │  view)  │ │           │ │ │adapter││adapter│││
│  └────┬────┘ └─────┬─────┘ │ └───┬───┘└───┬───┘││
│       │            │       └─────┼────────┼────┘│
│  ┌────▼────┐ ┌─────▼─────┐       │        │     │
│  │ SQLite  │ │ .superseded│       │        │     │
│  │ (state) │ │ /issues/   │       │        │     │
│  └─────────┘ │  *.md       │       │        │     │
│              └────────────┘       │        │     │
└──────────────────────────────────────────────────┘
                        │            │        │
                  ┌─────▼────────────▼────────▼───┐
                  │       Your Git Repos           │
                  │  (agents read/write code here) │
                  └────────────────────────────────┘
```

## In-Repo Data Model

Every repo gets a `.superseded/` directory:

```
.superseded/
├── config.yaml              # repo-specific settings
├── issues/
│   ├── SUP-001-refactor-auth.md
│   └── SUP-002-add-rate-limit.md
├── state.db                 # SQLite: pipeline state, logs
└── artifacts/
    ├── SUP-001/
    │   ├── spec.md
    │   ├── plan.md
    │   ├── diff.patch
    │   ├── test-results.json
    │   └── review.json
    └── SUP-002/
        └── ...
```

Tickets are markdown with YAML frontmatter:

```markdown
---
id: SUP-001
title: Refactor auth module
status: in-progress
stage: build
created: 2026-04-11
assignee: claude-code
labels: [backend, security]
---

## Description
Refactor the auth module to use JWT instead of sessions...

## Acceptance Criteria
- [ ] All existing auth tests pass
- [ ] JWT tokens issued with proper expiry
```

## Pipeline Stages

| Stage | Agent-Skills Mapping | Agent Action |
|-------|---------------------|--------------|
| **Spec** | idea-refine, spec-driven-development | Read ticket, write `spec.md` |
| **Plan** | planning-and-task-breakdown | Read spec, write `plan.md` |
| **Build** | incremental-implementation | Read plan, write code |
| **Verify** | test-driven-development, debugging | Write/fix tests, run them |
| **Review** | code-review-and-quality, security | Produce `review.json` |
| **Ship** | git-workflow, shipping | Commit, push, open PR |

Each stage:
1. Loads issue context (ticket + previous artifacts)
2. Invokes configured agent with skill-specific prompt
3. Captures structured output (files modified, test results, review notes)
4. Records pass/fail + artifacts
5. Auto-advances on success, halts on failure for human review

**Failure handling:** Issue pauses. Web UI shows failure + agent logs. Options: retry, skip, or override (edit artifacts manually and advance).

## Agent Adapters

```python
class AgentAdapter(Protocol):
    async def run(self, prompt: str, context: AgentContext) -> AgentResult: ...

class ClaudeCodeAdapter:
    """Spawns `claude` CLI with --print-mode and structured prompts"""

class OpenCodeAdapter:
    """Spawns `opencode` CLI with skill-loaded prompts"""
```

Each adapter:
- Takes prompt + context (repo path, issue files, skill definition)
- Spawns CLI as subprocess
- Streams stdout/stderr for real-time log capture
- Returns structured `AgentResult` (files changed, exit code, output text)
- Has configurable timeout (default: 10min per stage)

Default agent is configurable per issue or globally in `.superseded/config.yaml`.

## Web UI

| Route | Purpose |
|-------|---------|
| `/` | Dashboard: all issues, current stage, pass/fail status |
| `/issues/new` | Create new ticket (markdown editor) |
| `/issues/{id}` | Issue detail: ticket content, pipeline progress, artifacts |
| `/issues/{id}/stage/{stage}` | Stage detail: agent logs, diff, retry/skip controls |

- **SSE** for real-time pipeline progress
- **HTMX** for partial page updates (polling status, retry buttons)
- **Alpine.js** for minor interactivity (markdown preview, toggle panels)
- **Tailwind CSS** via CDN (no build step)

## Tech Stack

| Layer | Choice |
|-------|--------|
| **Runtime** | Python 3.12+, `uv` |
| **Web Framework** | FastAPI + Uvicorn |
| **Templates** | Jinja2 + HTMX + Alpine.js |
| **CSS** | Tailwind CDN |
| **Database** | SQLite via `aiosqlite` |
| **Tickets** | Markdown + YAML frontmatter (`python-frontmatter`) |
| **Agent Execution** | `asyncio.subprocess` |
| **Real-time** | Server-Sent Events |
| **Config** | YAML (`pyyaml`) |
| **Testing** | `pytest` + `pytest-asyncio` |

## Project Structure

```
superseded/
├── pyproject.toml
├── src/
│   └── superseded/
│       ├── __init__.py
│       ├── main.py                 # FastAPI app, startup
│       ├── config.py               # Settings from .superseded/config.yaml
│       ├── models.py               # Pydantic models (Issue, Stage, AgentResult)
│       ├── pipeline/
│       │   ├── __init__.py
│       │   ├── engine.py           # orchestrates stages
│       │   ├── stages.py           # stage definitions + transitions
│       │   └── prompts.py          # skill-to-prompt mapping
│       ├── agents/
│       │   ├── __init__.py
│       │   ├── base.py             # AgentAdapter protocol
│       │   ├── claude_code.py
│       │   └── opencode.py
│       ├── tickets/
│       │   ├── __init__.py
│       │   ├── reader.py           # parse markdown + frontmatter
│       │   └── writer.py           # update ticket status
│       ├── db.py                    # SQLite operations
│       └── routes/
│           ├── __init__.py
│           ├── dashboard.py
│           ├── issues.py
│           └── pipeline.py
├── templates/
│   ├── base.html
│   ├── dashboard.html
│   ├── issue_detail.html
│   ├── stage_detail.html
│   └── components/
│       ├── pipeline_progress.html
│       └── agent_log.html
├── static/
│   └── app.js
├── tests/
│   ├── test_pipeline.py
│   ├── test_agents.py
│   ├── test_tickets.py
│   └── test_routes.py
└── docs/
    └── plans/
```

## Competitive Positioning

Unlike GitHub Copilot (IDE-focused), Replit (hosted), or Aider (CLI-only), Superseded's unique angle is:
- Canonical per-issue markdown as single source of truth
- Agent pipelines that mirror the skill lifecycle (spec → ship)
- Web UI for visual pipeline control without leaving the terminal
- Local-first: your code stays on your machine, agents run locally