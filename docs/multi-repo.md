# Multi-Repo Support

Superseded supports tickets that span multiple repositories. A single issue can drive work across a frontend, backend, and any other configured repos.

## Configuration

Add named repos to `.superseded/config.yaml`:

```yaml
repo_path: /home/user/my-project          # primary (host) repo
repos:
  frontend:
    path: /home/user/my-frontend
  backend:
    path: /home/user/my-backend
```

The primary repo (where `.superseded/` lives) is always available implicitly.

## Ticket Format

Add a `repos` field to ticket frontmatter:

```yaml
---
id: SUP-001
title: Add user profile page
status: new
stage: spec
repos:
  - frontend
  - backend
---

Implement user profile with API endpoint and UI page.
```

Leave `repos` empty (or omit it) for single-repo tickets.

## Pipeline Behavior

| Stage | Behavior |
|-------|----------|
| SPEC | Runs once (primary repo) |
| PLAN | Runs once (primary repo) |
| BUILD | Runs once per target repo, in isolated worktrees |
| VERIFY | Runs once per target repo |
| REVIEW | Runs once per target repo |
| SHIP | Creates a PR per target repo |

## Artifact Structure

```
.superseded/
  artifacts/
    SUP-001/
      spec.md              # single (primary)
      plan.md              # single (primary)
      frontend/
        build_output.md    # per-repo
      backend/
        build_output.md    # per-repo
  worktrees/
    SUP-001                # primary worktree
    SUP-001__frontend      # frontend worktree
    SUP-001__backend       # backend worktree
```

## Database

Stage results include a `repo` column:

```sql
SELECT * FROM stage_results WHERE issue_id = 'SUP-001' AND repo = 'frontend';
```

## UI

The issue detail page shows per-repo progress when a ticket targets multiple repos.

## Troubleshooting

- **"Unknown repo" error**: Check that the repo name in the ticket matches a key in `config.yaml` repos.
- **Worktree conflicts**: Multi-repo worktrees use `{issue_id}__{repo}` naming to avoid collisions.
- **Missing results**: Query stage results with the `repo` filter to see per-repo status.
