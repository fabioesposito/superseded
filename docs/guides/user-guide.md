---
title: User Guide
category: guides
summary: Complete guide from first ticket to multi-repo pipelines
tags: [guide, getting-started]
date: 2026-04-11
---

# Superseded User Guide

Complete guide to using Superseded — from creating your first ticket to configuring multi-repo pipelines.

## Table of Contents

- [Overview](#overview)
- [Getting Started](#getting-started)
- [Creating Tickets](#creating-tickets)
- [Ticket Format](#ticket-format)
- [Pipeline Stages](#pipeline-stages)
- [Running the Pipeline](#running-the-pipeline)
- [Agent Configuration](#agent-configuration)
- [Project Rules](#project-rules)
- [Multi-Repo Support](#multi-repo-support)
- [Settings](#settings)
- [Authentication](#authentication)
- [Metrics](#metrics)
- [GitHub Issue Import](#github-issue-import)
- [The .superseded Directory](#the-superseded-directory)
- [Troubleshooting](#troubleshooting)

## Overview

Superseded is a local-first agentic pipeline tool. You write tickets (markdown specs), and the pipeline delegates implementation, testing, and release to AI agents running on your machine. The six-stage pipeline — Spec → Plan → Build → Verify → Review → Ship — mirrors the software development lifecycle, with each stage powered by vendored agent skills.

## Getting Started

### Install and Run

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
- At least one agent CLI: [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [OpenCode](https://github.com/opencodeco/opencode), or [Codex](https://github.com/openai/codex)
- `gh` CLI for the Ship stage (PR creation)
- [Playwright](https://playwright.dev/) for browser UI testing

## Creating Tickets

### Via Web UI

1. Open the dashboard at `http://127.0.0.1:8000/`
2. Click **"New Issue"** or navigate to `/issues/new`
3. Fill in the title, description, labels, and optionally select repos
4. Submit the form — the ticket is saved to `.superseded/issues/`

### Via CLI

Write a markdown file directly in `.superseded/issues/`:

```bash
cat > .superseded/issues/SUP-001-my-ticket.md << 'EOF'
---
id: SUP-001
title: Add health check endpoint
---

Create a /health endpoint that returns 200 OK with uptime info.
EOF
```

The `id` field is auto-generated when creating via the web UI. If writing manually, use the format `SUP-NNN`.

### Via GitHub Issue Import

On the New Issue page, paste a GitHub issue URL (e.g., `https://github.com/owner/repo/issues/123`) and click **"Import from GitHub"**. Superseded uses the `gh` CLI to fetch the issue title, body, labels, assignee, and comments.

## Ticket Format

Tickets are markdown files with YAML frontmatter stored in `.superseded/issues/`. The filename format is `{id}-{slug}.md`.

### Frontmatter Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `id` | string | yes | — | Unique identifier, format `SUP-NNN` |
| `title` | string | yes | — | Short description of the work |
| `status` | enum | no | `new` | Current status |
| `stage` | enum | no | `spec` | Current pipeline stage |
| `created` | date | no | today | ISO date string |
| `assignee` | string | no | `""` | Person or agent assigned |
| `labels` | list | no | `[]` | Categorization tags |
| `repos` | list | no | `[]` | Target repositories for multi-repo tickets |
| `github_url` | string | no | `""` | Original GitHub issue URL (set on import) |

### Status Values

- `new` — Ticket created, not yet started
- `in-progress` — Pipeline is running
- `paused` — A stage failed, awaiting retry or manual intervention
- `done` — All stages completed successfully
- `failed` — Pipeline exhausted retries

### Stage Values

Stages run in order: `spec` → `plan` → `build` → `verify` → `review` → `ship`

### Example: Bug Ticket

```markdown
---
id: SUP-042
title: Fix login redirect after password reset
status: new
stage: spec
created: "2026-04-11"
assignee: dev-team
labels:
  - bug
  - auth
  - priority-high
---

## Problem

After resetting their password, users are redirected to the home page
instead of their dashboard.

## Expected Behavior

1. User clicks "Forgot password"
2. Receives email, clicks reset link
3. Enters new password
4. Is redirected to /dashboard (not /)

## Acceptance Criteria

- [ ] Password reset redirects to /dashboard
- [ ] Session token is set after reset
- [ ] Existing tests pass
```

See [docs/tickets.md](tickets.md) for the complete specification.

## Pipeline Stages

Each stage is powered by a vendored agent skill:

| Stage | Skill | Purpose | Output |
|-------|-------|---------|--------|
| **Spec** | `spec-driven-development` | Generate detailed spec from ticket | `artifacts/{id}/spec.md` |
| **Plan** | `planning-and-task-breakdown` | Break spec into implementable tasks | `artifacts/{id}/plan.md` |
| **Build** | `incremental-implementation` | Implement code changes | Changes in worktree |
| **Verify** | `test-driven-development` | Run tests, fix failures | Changes in worktree |
| **Review** | `code-review-and-quality` | Review code quality and security | Review findings |
| **Ship** | `git-workflow-and-versioning` | Commit, push, create PR | PR via `gh` |

### How Stages Work

1. **Spec** — The agent reads the ticket body and writes `spec.md` to the artifacts directory.
2. **Plan** — The agent reads the spec and writes a structured `plan.md` with task breakdowns.
3. **Build** — The agent implements changes in an isolated git worktree.
4. **Verify** — The agent runs tests and fixes any failures.
5. **Review** — The agent reviews code quality and security. Critical/important findings loop back to Build.
6. **Ship** — The agent commits, pushes, and creates a PR via `gh pr create`.

### Context Assembly

Agents receive context in 7 progressive layers:

1. `AGENTS.md` — Repository guide
2. `docs/` index — Documentation file listing
3. Ticket — The issue markdown
4. Previous artifacts — Spec, plan outputs from prior stages
5. Project rules — `.superseded/rules.md`
6. Skill prompt — Stage-specific skill from `vendor/agent-skills/`
7. Error context — Previous errors (on retry)

### Retry Behavior

When a stage fails:
- The ticket status is set to `paused`
- You can retry the stage via the web UI by clicking the Retry button
- On retry, previous error output is injected into the agent prompt
- Retry is manual — you decide when and how many times to retry
- Retry is manual — you decide when and how many times to retry

### Await Input

During the Spec stage, the agent can pause and ask questions by writing a `questions.md` artifact. The ticket enters `paused` status with reason `awaiting-input`. You can answer the questions on the issue detail page, and the pipeline resumes automatically.

## Running the Pipeline

### Via Web UI

1. Navigate to the issue detail page (`/issues/{issue_id}`)
2. Click **"Advance"** to run the current stage
3. The UI shows a running indicator with auto-refresh via HTMX polling
4. When the stage completes, the page updates with results
5. Continue advancing through stages until done

### Stage Detail Pages

Click any stage in the progress indicator to see detailed results including:
- Agent output (stdout/stderr)
- Files changed
- Execution duration
- Pass/fail status

### Real-Time Updates

The dashboard uses Server-Sent Events (SSE) to push pipeline progress to the browser without polling. Stage events stream to the issue detail page in real time.

## Agent Configuration

### Default Agent

Set the default agent and model in `.superseded/config.yaml`:

```yaml
default_agent: opencode       # or "claude-code", "codex"
default_model: opencode-go/kimi-k2.5
```

### Per-Stage Agent Selection

Override the agent and model for individual stages:

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

### Via Settings UI

Navigate to `/settings` to configure agents and repos from the browser. Changes are saved to `.superseded/config.yaml` and take effect immediately — the pipeline reloads automatically.

### Available Agent CLIs

| CLI | Command | Notes |
|-----|---------|-------|
| Claude Code | `claude` | Anthropic's agent CLI |
| OpenCode | `opencode` | Open-source agent CLI |
| Codex | `codex` | OpenAI's agent CLI |

Each CLI must be installed and available on `$PATH` before Superseded can use it.

## Project Rules

Create `.superseded/rules.md` to define non-negotiable project rules injected into every agent prompt:

```markdown
# Project Rules

- Run the full test suite before committing
- Write tests for every new feature
- Keep functions under 30 lines
- Use type hints on all function signatures
- Never commit secrets or credentials
```

These rules are included in the context for every pipeline stage.

## Multi-Repo Support

Tickets can target multiple repositories. Set `repos` in ticket frontmatter to fan out BUILD/VERIFY/REVIEW stages across repos.

### Configuration

```yaml
# .superseded/config.yaml
repos:
  frontend:
    path: /home/user/my-frontend
  backend:
    path: /home/user/my-backend
```

### Multi-Repo Ticket

```yaml
---
id: SUP-001
title: Add user profile page
repos:
  - frontend
  - backend
---
```

### Pipeline Behavior

| Stage | Behavior |
|-------|----------|
| SPEC | Runs once (primary repo) |
| PLAN | Runs once (primary repo) |
| BUILD | Runs once per target repo, in isolated worktrees |
| VERIFY | Runs once per target repo |
| REVIEW | Runs once per target repo |
| SHIP | Creates a PR per target repo |

See [docs/multi-repo.md](multi-repo.md) for full documentation.

## Settings

The `/settings` page provides a web UI for configuration:

- **Repos** — Add, configure, and remove named repositories
- **Per-stage agents** — Select CLI and model for each pipeline stage
- **GitHub token** — Set or update the `github_token` used for PR creation and issue import

All changes are persisted to `.superseded/config.yaml` and take effect immediately.

## Authentication

Superseded supports optional API key authentication. Set the `api_key` in `.superseded/config.yaml` or via the `SUPERSEDED_API_KEY` environment variable:

```yaml
# .superseded/config.yaml
api_key: your-secret-key
```

Or:

```bash
export SUPERSEDED_API_KEY=your-secret-key
```

When an API key is configured, all requests must include an `X-API-Key` header matching the configured value. The `/health` and `/static` paths are exempt from authentication.

When no API key is set, Superseded runs without authentication (suitable for local development).

## Metrics

Navigate to `/pipeline/metrics` (or `/metrics`) to view pipeline metrics:

- Total issues by status
- Stage success rates
- Retry counts by stage
- Per-stage duration stats

Metrics are also available as JSON at `/api/pipeline/metrics`.

## GitHub Issue Import

Superseded can import GitHub issues as tickets:

1. Navigate to `/issues/new`
2. Paste a GitHub issue URL (e.g., `https://github.com/owner/repo/issues/123`)
3. Click **"Import from GitHub"**
4. The issue title, body, labels, assignee, and comments are fetched via `gh` CLI
5. Edit the imported content and submit

The `github_url` field in the ticket frontmatter records the original issue URL.

## The .superseded Directory

Superseded stores all runtime data in `.superseded/` within your repository:

```
.superseded/
  config.yaml          # Project configuration
  rules.md             # Non-negotiable project rules
  issues/              # Ticket files (canonical source of truth)
    SUP-001-add-health-check.md
    SUP-042-fix-login.md
  artifacts/           # Stage output artifacts
    SUP-001/
      spec.md           # Generated spec
      plan.md           # Generated plan
      questions.md      # Agent questions (if awaiting input)
      answers.md        # User answers (if answered)
  worktrees/           # Isolated git worktrees per issue
  state.db             # SQLite state cache (derived from markdown)
```

### Key Principles

- **Markdown is canonical** — Issues and their state live in `.md` files; SQLite is a fast index/cache
- **Artifacts are per-issue** — Each issue gets its own subdirectory under `artifacts/`
- **Worktrees are ephemeral** — Git worktrees are created for BUILD/VERIFY/REVIEW stages and cleaned up after SHIP

## Troubleshooting

### Agent not found

Make sure the agent CLI is installed and on `$PATH`. Test with:

```bash
which claude   # For Claude Code
which opencode # For OpenCode
which codex    # For Codex
```

### Stage fails immediately

Check the stage detail page for the agent's error output. Common causes:

- Agent CLI not installed or not on `$PATH`
- Worktree creation failed (ensure the repo has no uncommitted changes that conflict)
- Agent produced insufficient output (< 50 chars)

### GitHub integration issues

- Ensure `gh` CLI is installed and authenticated (`gh auth status`)
- For PR creation in the Ship stage, set `github_token` in config or the `GITHUB_TOKEN` environment variable
- GitHub issue import requires `gh` CLI access to the source repository

### Worktree conflicts

If worktrees are left behind from a failed run:

```bash
# List worktrees
git worktree list

# Remove stale worktrees
git worktree prune
```

### Database issues

Since SQLite is a cache derived from markdown, you can recreate it:

```bash
# Stop the server
# Delete the database
rm .superseded/state.db
# Restart — the database is rebuilt from markdown files
uv run superseded
```

### Port already in use

Specify a different port:

```bash
uv run superseded --port 3000
```

Or change `port` in `.superseded/config.yaml`.