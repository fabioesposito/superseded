---
title: Testing
category: operations
summary: Test suite overview — pytest and Playwright e2e
tags: [testing, e2e, pytest, playwright]
date: 2026-04-22
---

# Testing

Superseded has two test layers: a Python pytest suite for backend logic and a Playwright e2e suite for UI validation.

## pytest

```bash
uv run pytest tests/ -v
```

The pytest suite covers models, database operations, ticket CRUD, pipeline service logic, CSRF middleware, and auth.

## Playwright e2e

```bash
npx playwright test
```

The e2e suite targets `http://localhost:8000/` (or `http://0.0.0.0:8000/`). **Start the server before running the tests.**

### Test files

| File | Focus |
|------|-------|
| `e2e/dashboard.spec.ts` | Stage counters, filtering, navigation to issue detail |
| `e2e/issues.spec.ts` | New issue form, CSRF, creation, validation, assignee dropdown |
| `e2e/issue-detail.spec.ts` | Issue detail rendering, stage detail page, delete action |
| `e2e/pipeline.spec.ts` | Advance/retry confirm dialogs, status/events endpoints, SSE streams |
| `e2e/settings-mutations.spec.ts` | Add/delete repos, token, API keys, source root, notifications, server settings |
| `e2e/settings.spec.ts` | Settings page display, agent config defaults, agent config save |
| `e2e/import-metrics.spec.ts` | GitHub import errors, metrics dashboard, metrics/issues/health API endpoints |
