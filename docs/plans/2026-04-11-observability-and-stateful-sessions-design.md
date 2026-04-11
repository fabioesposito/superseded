# Observability & Stateful Sessions Design

## Problem

Superseded agents are fire-and-forget CLI subprocesses. No conversation history persists between runs, and the UI only shows final results after process exit. This makes it impossible to:
- Understand what an agent did during a long-running stage
- Build on prior context across pipeline runs
- Diagnose failures without re-running
- Track aggregate performance metrics

## Approach

SQLite-Everything: extend the existing database with two new tables, make the agent adapter stream events line-by-line, and surface live logs + metrics in the web UI.

## Data Model

### New Tables

```sql
CREATE TABLE IF NOT EXISTS session_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    role TEXT NOT NULL,          -- 'user' | 'assistant' | 'system'
    content TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',  -- token count, duration, file changes
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (issue_id) REFERENCES issues(id)
);

CREATE TABLE IF NOT EXISTS agent_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    event_type TEXT NOT NULL,    -- 'stdout' | 'stderr' | 'status' | 'metric'
    content TEXT DEFAULT '',
    metadata TEXT DEFAULT '{}',  -- line number, timestamp offset, exit code
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (issue_id) REFERENCES issues(id)
);
```

### New Pydantic Models

```python
class SessionTurn(BaseModel):
    role: str          # user | assistant | system
    content: str
    stage: Stage
    attempt: int
    metadata: dict = {}

class AgentEvent(BaseModel):
    event_type: str    # stdout | stderr | status | metric
    content: str = ""
    stage: Stage
    metadata: dict = {}

class PipelineMetrics(BaseModel):
    total_issues: int
    issues_by_status: dict[str, int]
    stage_success_rates: dict[str, float]
    avg_stage_duration_ms: dict[str, float]
    total_retries: int
    retries_by_stage: dict[str, int]
    recent_events: list[AgentEvent]
```

## Streaming Agent Adapter

### AgentAdapter Protocol Change

Add `run_streaming()` to the protocol. Default implementation wraps `run()` for backward compatibility.

```python
async def run_streaming(
    self, prompt: str, context: AgentContext
) -> AsyncIterator[AgentEvent]:
    """Yield events as agent executes. Default: wraps run()."""
    result = await self.run(prompt, context)
    for line in result.stdout.splitlines():
        yield AgentEvent(event_type="stdout", content=line, stage=context.issue.stage)
    yield AgentEvent(
        event_type="status",
        metadata={"exit_code": result.exit_code},
        stage=context.issue.stage,
    )
```

### SubprocessAgentAdapter Changes

New `run_streaming()` reads stdout/stderr with async line readers concurrently:

1. Spawn subprocess with `PIPE` for stdout/stderr
2. Create two async tasks: one reads stdout lines, one reads stderr lines
3. Each line yielded as `AgentEvent(event_type="stdout"/"stderr", content=line)`
4. On process exit, yield final `AgentEvent(event_type="status", metadata={"exit_code": ..., "duration_ms": ...})`
5. `run()` stays unchanged for non-UI contexts

### HarnessRunner Changes

- `run_stage_with_retries()` gains a `_run_stage_streaming()` variant
- Before execution: write prompt as `SessionTurn(role="user")`
- During execution: write each `AgentEvent` to `agent_events` table + publish to SSE queue
- After completion: write condensed stdout as `SessionTurn(role="assistant")`
- Populate `metadata.summary` with key info (files changed, exit code, duration)

## Live SSE Streaming

### Event Queue Architecture

`PipelineEventManager` class:
- Manages `dict[str, asyncio.Queue]` keyed by issue_id
- Queue created on stage start, removed on stage end
- `publish(issue_id, event)` puts event into queue
- `subscribe(issue_id)` returns async generator consuming from queue
- If no subscribers, events still go to SQLite (no data loss)

### Endpoints

**`GET /pipeline/issues/{issue_id}/events/stream`** — Live event stream
- SSE endpoint consuming from PipelineEventManager queue
- Yields `{"event": "stdout", "data": "..."}` for each agent event
- Final `{"event": "done", "data": "{...metrics...}"}` on stage completion
- Auto-reconnects on disconnect

**`GET /pipeline/issues/{issue_id}/events`** — Historical events
- Returns last N events from `agent_events` table as JSON
- Used to populate log viewer on page load before stream connects

### UI Changes

- Issue detail page gets `<div id="agent-log">` container
- HTMX SSE extension listens to stream, appends log lines with `hx-swap="beforeend"`
- Log lines styled: stdout in default, stderr in red, status in bold
- When `done` event arrives, HTMX refreshes stage status panel
- Log viewer shows historical events on load, then live-streams new ones

## Session History

### ContextAssembler Changes

New `_build_session_history_layer()`:
- Queries `session_turns` for the same issue, from *prior* stages only
- Truncates assistant outputs to 2000 chars each
- Formats as:
  ```
  ## Previous Session History

  ### spec (attempt 1)
  **You asked:** [prompt summary]
  **Agent said:** [truncated output]
  ```
- Sits between "Previous Stage Artifacts" and "Project Rules" in context stack

### HarnessRunner Session Logging

- Before agent execution: `save_session_turn(role="user", content=prompt)`
- After agent execution: `save_session_turn(role="assistant", content=stdout[:2000], metadata={"files_changed": [...], "exit_code": ...})`
- On retry: each attempt gets its own turn pair

## Metrics Dashboard

### Metrics Endpoint

**`GET /pipeline/metrics`** — Aggregate pipeline metrics

Queries:
- `issues` table → total count, counts by status
- `stage_results` → success rates per stage, avg duration
- `harness_iterations` → retry counts total + per stage
- `agent_events` → recent activity feed (last 20 events)

### Dashboard Template

`templates/metrics.html`:
- Success/failure rate per stage as progress bars (no JS chart libs)
- Average duration per stage
- Retry counts (total + per stage)
- Recent agent activity feed (auto-refresh via HTMX poll)
- No external dependencies — pure HTML/CSS with `<progress>` elements

## File Changes Summary

| File | Change |
|------|--------|
| `src/superseded/models.py` | Add `SessionTurn`, `AgentEvent`, `PipelineMetrics` models |
| `src/superseded/db.py` | Add `session_turns` + `agent_events` tables, CRUD methods |
| `src/superseded/agents/base.py` | Add `run_streaming()` to protocol + `SubprocessAgentAdapter` |
| `src/superseded/pipeline/harness.py` | Streaming variant, session turn logging, event publishing |
| `src/superseded/pipeline/context.py` | Add `_build_session_history_layer()` |
| `src/superseded/pipeline/events.py` | New: `PipelineEventManager` class |
| `src/superseded/routes/pipeline.py` | Add SSE stream endpoint, historical events endpoint, metrics endpoint |
| `templates/issue_detail.html` | Add live log viewer with SSE |
| `templates/metrics.html` | New: metrics dashboard |

## Future: Multi-Turn Steering

Not in scope for this iteration, but the design supports it:
- `SessionManager` class wraps a long-running subprocess with stdin/stdout pipes
- Same `session_turns` table, turns written in real-time
- New endpoint: `POST /pipeline/issues/{issue_id}/session/send` to inject user message mid-execution
- Agent sees new message via stdin, responds via stdout, both logged
