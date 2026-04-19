# Ticket Format

Tickets are markdown files with YAML frontmatter. They live in `.superseded/issues/` and are the single source of truth for the pipeline.

## File Naming

```
.superseded/issues/{id}-{slug}.md
```

Examples:
- `.superseded/issues/SUP-001-add-user-profile.md`
- `.superseded/issues/SUP-042-fix-login-redirect.md`

The slug is derived from the title: lowercase, non-alphanumeric characters replaced with hyphens.

## Frontmatter Fields

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

### Status Values

- `new` — Ticket created, not yet started
- `in-progress` — Pipeline is running
- `paused` — A stage failed, awaiting retry or manual intervention
- `done` — All stages completed successfully
- `failed` — Pipeline exhausted retries

### Stage Values

Stages run in order: `spec` → `plan` → `build` → `verify` → `review` → `ship`

### Repos

List of repo names that must match keys in `.superseded/config.yaml` `repos` map. Leave empty or omit for single-repo tickets.

## Minimal Ticket

```markdown
---
id: SUP-001
title: Add health check endpoint
---

Create a /health endpoint that returns 200 OK with uptime info.
```

## Full Ticket

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
instead of their dashboard. This breaks the expected login flow.

## Expected Behavior

1. User clicks "Forgot password"
2. Receives email, clicks reset link
3. Enters new password
4. Is redirected to `/dashboard` (not `/`)

## Acceptance Criteria

- [ ] Password reset redirects to `/dashboard`
- [ ] Session token is set after reset
- [ ] Works on mobile and desktop
- [ ] Existing tests pass
```

## Multi-Repo Ticket

```markdown
---
id: SUP-100
title: Add user profile page
status: new
stage: spec
repos:
  - frontend
  - backend
---

## Overview

Add a user profile page that displays user info and allows editing.

## Backend

- GET /api/users/:id — returns user profile
- PUT /api/users/:id — updates profile fields
- Validate input, require auth

## Frontend

- Profile page at /profile
- Edit form with name, bio, avatar
- Fetch data from backend API
- Show loading/error states
```

For multi-repo tickets, the `repos` field must list repo names defined in the config:

```yaml
# .superseded/config.yaml
repos:
  frontend:
    path: /home/user/frontend
  backend:
    path: /home/user/backend
```

## Creating Tickets

**Via web UI:** Navigate to `/issues/new` and fill out the form. The repos field accepts comma-separated names.

**Via CLI:** Write the markdown file directly:

```bash
cat > .superseded/issues/SUP-001-my-ticket.md << 'EOF'
---
id: SUP-001
title: My ticket
---

Description here.
EOF
```

**Via gh issue (sync):** Tickets can be created from GitHub issues. The body becomes the markdown content below the frontmatter.

## What Happens When a Ticket Is Processed

1. **SPEC** — Agent reads the ticket body and writes a detailed spec to `.superseded/artifacts/{id}/spec.md`
2. **PLAN** — Agent reads the spec and writes a task breakdown to `.superseded/artifacts/{id}/plan.md`
3. **BUILD** — Agent implements changes in an isolated git worktree
4. **VERIFY** — Agent runs tests and fixes failures
5. **REVIEW** — Agent reviews code quality and security
6. **SHIP** — Agent commits, pushes, and creates a PR via `gh pr create`

The `status` and `stage` fields in frontmatter are updated automatically as the pipeline progresses.
