---
title: Architecture Improvements Design
category: adrs
summary: Architecture Improvements Design
tags: []
date: 2026-04-15
---

# Architecture Improvements Design

Date: 2026-04-15

## Overview

Address 4 code quality and architecture issues identified during review:

1. Replace ad-hoc DB migrations with Alembic
2. Replace hardcoded agent factory with registry pattern
3. Split routes into `api/` and `web/` packages
4. Remove hardcoded model defaults

## 1. Database Migrations (Alembic)

### Problem

`db.py` manages schema creation via `CREATE TABLE IF NOT EXISTS` and schema evolution via a hardcoded list of `ALTER TABLE` statements inside `try/except OperationalError` blocks. No version tracking, no rollback, no migration history.

### Solution

- Add `alembic` as a dependency
- Initialize Alembic at project root with `alembic init migrations`
- Create initial migration capturing current schema (5 tables: issues, stage_results, harness_iterations, session_turns, agent_events)
- Replace `Database.initialize()` schema creation + migration block with a call to `alembic.command.upgrade(config, "head")`
- Remove the manual `migrations` list and `ALTER TABLE` try/except blocks from `db.py`
- `Database.initialize()` still creates the `aiosqlite` connection and sets WAL mode, but delegates DDL to Alembic
- Alembic runs synchronously at app startup (inside `lifespan`), before async connection pool opens

### Migration Strategy

- Initial migration captures exact current schema so existing databases are recognized as "at head"
- Use `alembic stamp head` for existing databases to mark them as already migrated
- Future schema changes get proper numbered migrations with upgrade/downgrade

## 2. Agent Registry Pattern

### Problem

`AgentFactory.create()` in `factory.py` uses a hardcoded `if/elif` chain. Adding a new agent requires modifying the factory.

### Solution

- Create module-level `_registry: dict[str, type[SubprocessAgentAdapter]]` in `agents/__init__.py`
- Add `register_agent(name: str)` decorator function
- Each adapter decorates itself: `@register_agent("claude-code")` on `ClaudeCodeAdapter`
- `AgentFactory.create()` looks up the registry and instantiates with kwargs
- Import all adapter modules in `agents/__init__.py` so `@register_agent` runs at import time

### Before/After

Before:
```python
# factory.py
def create(self, cli=None, model=None):
    if cli == "claude-code":
        return ClaudeCodeAdapter(...)
    elif cli == "opencode":
        return OpenCodeAdapter(...)
    elif cli == "codex":
        return CodexAdapter(...)
    raise ValueError(f"Unknown agent CLI: {cli}")
```

After:
```python
# __init__.py
_registry: dict[str, type[SubprocessAgentAdapter]] = {}

def register_agent(name: str):
    def decorator(cls):
        _registry[name] = cls
        return cls
    return decorator

def get_registry():
    return _registry

# factory.py
def create(self, cli=None, model=None):
    cli = cli or self.default_agent
    registry = get_registry()
    if cli not in registry:
        raise ValueError(f"Unknown agent: {cli}")
    return registry[cli](model=model, timeout=self.timeout, ...)

# claude_code.py
@register_agent("claude-code")
class ClaudeCodeAdapter(SubprocessAgentAdapter): ...
```

## 3. Route Separation (routes/api/ + routes/web/)

### Problem

`pipeline.py` (400 lines) mixes JSON API endpoints alongside HTML/HTMX rendering endpoints. Same pattern in other route files.

### Solution

Split into two sub-packages:

```
routes/
  __init__.py          (keep: get_templates, _csrf_token_for_request)
  deps.py              (keep: Deps, PipelineState, get_deps + shared helpers)
  auth.py              (keep: unchanged)
  csrf.py              (keep: unchanged)
  api/
    __init__.py
    pipeline.py         # JSON: /api/pipeline/metrics, events, event stream
  web/
    __init__.py
    dashboard.py        # HTML routes from current dashboard.py
    issues.py           # HTML routes from current issues.py
    pipeline.py         # HTML/HTMX routes from current pipeline.py
    settings.py         # HTML routes from current settings.py
```

### Details

- `api/pipeline.py` gets: `GET /api/pipeline/metrics`, `GET /api/pipeline/issues/{id}/events`, `GET /api/pipeline/issues/{id}/events/stream`
- `web/pipeline.py` gets: `POST /pipeline/issues/{id}/advance`, `POST /pipeline/issues/{id}/retry`, `GET /pipeline/issues/{id}/status`, `GET /pipeline/metrics` (dashboard), `GET /pipeline/sse/dashboard`
- Shared helpers (`_find_issue`, `_get_executor`, `_get_event_manager`, `_render_issue_detail_oob`, `_running`) move to `routes/deps.py`
- `main.py` imports from both packages
- `_run_and_advance` (referenced from `issues.py`) moves to `deps.py` or a shared `pipeline_helpers.py`

## 4. Remove Hardcoded Model Defaults

### Problem

`config.py` hardcodes `model: str = "opencode-go/kimi-k2.5"` in `StageAgentConfig` and `default_model: str = "opencode-go/kimi-k2.5"` in `SupersededConfig`. If this model is deprecated, new users get silent failures.

### Solution

- Change both defaults to empty strings (`""`)
- Each adapter defines its own `DEFAULT_MODEL` class variable with a sensible fallback
- Adapters only include `--model` flag when a model is explicitly set or their DEFAULT_MODEL applies
- When model is empty and no DEFAULT_MODEL, the CLI binary uses its own default
- Settings UI and YAML config remain the override mechanism

### Adapter Defaults

| Adapter         | DEFAULT_MODEL              | Behavior when empty |
|-----------------|----------------------------|---------------------|
| ClaudeCode      | `claude-sonnet-4-20250514` | Always passes model |
| OpenCode        | `""`                        | Omits --model flag  |
| Codex           | `o4-mini`                   | Always passes model |

### Config Change

```python
class StageAgentConfig(BaseModel):
    cli: str = "opencode"
    model: str = ""  # was "opencode-go/kimi-k2.5"

class SupersededConfig(BaseModel):
    default_model: str = ""  # was "opencode-go/kimi-k2.5"
```

## Testing

- All 4 changes must maintain the existing 258 passing tests
- Alembic: add test that verifies `alembic upgrade head` creates all expected tables
- Registry: add test that registry contains all 3 adapters, and that unknown cli raises ValueError
- Route split: move route test imports to new paths, verify all route tests pass
- Defaults: update any tests that assert on the old default model value