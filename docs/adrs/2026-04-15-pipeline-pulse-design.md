---
title: Pipeline Pulse — Design
category: adrs
summary: Pipeline Pulse — Design
tags: []
date: 2026-04-15
---

# Pipeline Pulse — Design

Three tightly composed features: stage duration metrics, pipeline timeline visualization, and ntfy.sh push notifications. Each makes the others more valuable.

## 1. Stage Duration Metrics

**Problem**: `avg_stage_duration_ms` is always `{}`. No timing data is persisted.

**Solution**:

- Add `started_at` and `completed_at` columns to `stage_results` table (nullable timestamps, with migration for existing rows).
- `StageExecutor` records `started_at` when a stage begins and `completed_at` when it finishes (success or failure).
- A new `DurationMetrics` model computes: avg, median, p95 per stage type, plus overall pipeline duration.
- The existing `/pipeline/metrics` route returns durations alongside the existing health/success-rate data.
- Stage detail pages show "Took 2m 34s" or "Still running (3m 12s)".

**DB change**: Two `ALTER TABLE ADD COLUMN` statements on `stage_results` — zero disruption to existing data.

## 2. Pipeline Timeline Visualization

**Problem**: Issue detail page shows a progress bar and stage results, but no sense of *when* things happened or how long each stage took.

**Solution**:

- New partial template `_timeline.html` rendered on the issue detail page.
- Each stage appears as a segment showing: stage name, start time → end time, duration, and status (completed/failed/running/pending).
- Retry iterations are stacked under the same stage label (e.g., "VERIFY (attempt 2/3)").
- The current/paused stage gets a pulsing indicator.
- Rendered server-side with HTMX — the SSE stream can trigger `hx-get` to re-render just the timeline partial.
- Minimal CSS: a horizontal bar with colored segments, width proportional to duration. Pending stages are gray outlines.
- Uses the `started_at`/`completed_at` data from section 1.

## 3. ntfy.sh Push Notifications

**Problem**: You have to watch the dashboard to know when a stage finishes, fails, or pauses. No push awareness.

**Solution**:

- New `NotificationService` class in `src/superseded/notifications.py` with a single `notify(topic, title, message, priority, tags)` method.
- Uses `httpx` (already a dep via FastAPI) to POST to `https://ntfy.sh/{topic}` — no auth, no signup needed.
- Events that trigger notifications:
  - Stage **completed** (priority: default) — "SUP-001: BUILD completed (2m 34s)"
  - Stage **failed** (priority: high) — "SUP-001: BUILD failed after 3 attempts"
  - Pipeline **paused** (priority: high) — "SUP-001: PAUSED — awaiting input"
  - Pipeline **shipped** (priority: default) — "SUP-001: SHIPPED!"
- Config in `.superseded/config.yaml`:
  ```yaml
  notifications:
    enabled: true
    ntfy_topic: "superseded-myproject"
  ```
- Empty/disabled by default — zero noise unless you opt in.
- Called from `StageExecutor` after each stage completes, and from `HarnessRunner` on pause/ship.
- `ntfy.sh` supports actions (e.g., "View" button linking to `http://localhost:8000/issues/SUP-001`) via the `Actions` header.
- **Settings page**: ntfy config (enabled toggle, topic input) exposed on the `/settings` page alongside existing API keys and GitHub token. Persisted to `config.yaml` via `save_config()`.

## Data Model Changes

```python
# stage_results table migration
ALTER TABLE stage_results ADD COLUMN started_at TEXT;
ALTER TABLE stage_results ADD COLUMN completed_at TEXT;
```

```python
# config.yaml addition
notifications:
  enabled: false
  ntfy_topic: null
```

## New Files

- `src/superseded/notifications.py` — NotificationService
- `templates/_timeline.html` — Timeline partial

## Modified Files

- `src/superseded/db.py` — Migration, duration query methods
- `src/superseded/models.py` — DurationMetrics model, started_at/completed_at on StageResult
- `src/superseded/pipeline/executor.py` — Record timestamps, call notifications
- `src/superseded/pipeline/harness.py` — Call notifications on pause/ship
- `src/superseded/config.py` — Notifications config model
- `src/superseded/routes/settings.py` — ntfy settings endpoints
- `src/superseded/routes/dashboard.py` — Return duration data
- `src/superseded/routes/pipeline.py` — Duration data in stage detail
- `templates/settings.html` — ntfy settings UI
- `templates/issue_detail.html` — Include timeline partial
- `templates/_results.html` — Show duration on completed stages
- `templates/metrics.html` —_DURATION_METRICS section