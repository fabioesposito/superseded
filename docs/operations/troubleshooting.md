---
title: Troubleshooting
category: operations
summary: Common issues and fixes
tags: [troubleshooting, debugging]
date: 2026-04-19
---

# Troubleshooting

## Common Issues

### Agent subprocess fails silently

Check `.superseded/state.db` for the stage result. The `error` column contains the failure message.

### Worktree conflicts

Worktrees use `{issue_id}__{repo}` naming. If a worktree already exists from a previous run,
delete it: `rm -rf .superseded/worktrees/{name}`.

### Pipeline stuck in paused state

Reset the ticket status to `new` and stage to `spec` in the ticket frontmatter.
