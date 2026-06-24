# Harness Upgrade Design: Making Superseded the Easiest Agent Harness

**Date:** 2026-05-04
**Status:** Approved for implementation
**Approach:** Hybrid B+C — phased delivery with harness abstraction introduced in Phase 2

## Problem

Superseded covers all six agent harness dimensions (context engineering, tool orchestration, state management, verification, HITL, lifecycle) but has significant gaps in each. Measured against the [Harness Engineering Complete Guide](https://harness-engineering.ai/blog/agent-harness-complete-guide/), Superseded is a capable prototype that needs production-grade infrastructure to become the easiest-to-use agent harness.

## Current State Assessment

| Dimension | Strength | Key Gap |
|---|---|---|
| Context Engineering | 10-layer progressive context, skill-based prompts | No token budgeting, no compression, no rules.md |
| Tool Orchestration | 4 agents, Docker sandbox, worktree isolation | No tool restrictions, no container resource limits |
| State & Memory | Markdown-canonical dual-source, full audit trail | No checkpoint/resume mid-stage, no event pruning |
| Verification | Multi-signal (exit code + output + artifacts) | No automated test enforcement, no review parsing |
| Human-in-the-Loop | 3 patterns (questions, approvals, retry) | All-or-nothing approval, single-user only |
| Lifecycle | Timeouts, concurrency locks, SSE streaming | No health monitoring, no graceful shutdown, no persistent queue |

## Design: Four Phases

Each phase is independently shippable. Phases 0-1 build on current architecture. Phase 2 introduces a Harness abstraction. Phases 3-4 extend it.

---

### Phase 0: First-Run UX

**Goal:** `uv run superseded` works out of the box with minimal configuration.

**Changes:**

1. **Config validation on startup.** `config.py` validates required fields and reports clear errors: "No agent configured. Run `superseded init` or visit /settings to configure."

2. **Sensible defaults.** Default config works for local-only mode with claude-code agent. No API keys required for local development (agent uses ambient credentials).

3. **`superseded init` CLI command.** Scaffolds `.superseded/` directory with:
   - `config.yaml` with sensible defaults
   - `rules.md` template with common rules
   - `issues/` directory
   - Example ticket

4. **`rules.md` editor in Settings UI.** Textarea at `/settings` for editing `.superseded/rules.md`. Rules are injected into every agent prompt via the existing ContextAssembler rules layer.

5. **Setup wizard in web UI.** On first visit to `/settings`, detect missing API keys and agent availability. Show inline guidance: "claude-code detected ✓", "No ANTHROPIC_API_KEY set — add below or use ambient credentials."

**Files modified:**
- `src/superseded/config.py` — add validation, defaults
- `src/superseded/cli.py` — new file, `superseded init` command
- `src/superseded/routes/web/settings.py` — rules editor, setup wizard
- `templates/settings.html` — rules textarea, agent detection UI

---

### Phase 1: Verification Loops

**Goal:** Structured verification after each stage. The single highest-impact improvement per the article.

**Changes:**

1. **Artifact content validation.** After SPEC stage, verify `spec.md` contains required sections: `## Problem`, `## Solution`, `## Requirements`. After PLAN stage, verify `plan.md` contains `## Tasks` or equivalent structure. Validation is configurable per stage in config.yaml:

   ```yaml
   stages:
     spec:
       verify:
         required_sections: ["Problem", "Solution", "Requirements"]
     plan:
       verify:
         required_sections: ["Tasks"]
   ```

2. **Review severity parsing.** Parse REVIEW stage output for `## Critical`, `## Important`, `## Nit`, `## FYI` headings. Count findings per severity. Configurable threshold:

   ```yaml
   stages:
     review:
       verify:
         max_critical_findings: 0
         max_important_findings: 3
   ```

   If thresholds exceeded, stage fails with structured error: "2 Critical findings block merge. Fix: [finding summaries]."

3. **Test result parsing.** Parse VERIFY stage output for common test frameworks (pytest, jest, cargo test, go test). Extract pass/fail/error counts. Surface in UI as structured data alongside raw output.

4. **Structured error feedback.** Failed verification produces machine-readable errors:

   ```json
   {
     "verification_failures": [
       {"type": "missing_section", "stage": "spec", "section": "Requirements"},
       {"type": "critical_findings", "count": 2, "findings": ["..."]}
     ]
   }
   ```

   These are injected into the retry prompt: "The previous attempt failed verification. Fix these specific issues: ..."

5. **VerificationEngine class.** Encapsulate verification logic in a new module `src/superseded/verification.py`. This class is used by the current harness and becomes the foundation for Phase 2's Harness abstraction.

**Files modified:**
- `src/superseded/verification.py` — new, VerificationEngine class
- `src/superseded/pipeline/harness.py` — integrate VerificationEngine
- `src/superseded/pipeline/prompts.py` — add section expectations to stage prompts
- `src/superseded/config.py` — add `verify:` schema to stage config
- `templates/_stage_result.html` — show structured verification results

---

### Phase 2: Harness Abstraction + Checkpoint/Resume

**Goal:** Extract a Harness class that owns the full agent lifecycle. Add checkpoint/resume for long-running stages.

**Architecture:**

```
src/superseded/harness/
  __init__.py          # Harness class — public interface
  context.py           # ContextAssembler (moved from pipeline/context.py)
  verification.py      # VerificationEngine (moved from verification.py)
  checkpoint.py        # CheckpointManager
  lifecycle.py         # LifecycleManager (timeouts, signals, resources)
```

**Harness class interface:**

```python
class Harness:
    def __init__(self, config, db, agents, worktree_mgr): ...

    async def run_stage(self, issue: Issue, stage: str, config: StageConfig) -> StageResult:
        """Execute a full stage lifecycle: context → agent → verify → checkpoint."""

    async def checkpoint(self, issue_id: str, stage: str, state: dict) -> None:
        """Snapshot current working state to .superseded/checkpoints/{id}/{stage}.json."""

    async def resume(self, issue_id: str, stage: str) -> dict | None:
        """Load checkpoint if it exists and preconditions hold. None if stale."""

    async def verify(self, stage: str, artifacts: dict, output: str) -> VerificationResult:
        """Run verification engine on stage output."""

    async def shutdown(self) -> None:
        """Graceful shutdown: signal agents, save state, release resources."""
```

**Checkpoint/Resume mechanism:**

- During BUILD/VERIFY/REVIEW stages, the Harness snapshots working state every 2 minutes
- Checkpoint data: `{files_changed: [...], current_task: "implement auth", plan_progress: {task_1: "done", task_2: "in_progress"}, timestamp: "..."}`
- Stored in `.superseded/checkpoints/{issue_id}/{stage}.json`
- On crash/restart: Harness checks for checkpoint, validates preconditions (git worktree exists, files match expected state), resumes with context: "Resuming from checkpoint. Completed tasks: [list]. Continue from: [current_task]."
- If preconditions fail: discard checkpoint, restart fresh with context about what was lost

**Migration path:**
- `pipeline/harness.py` and `pipeline/executor.py` are refactored into the Harness class. Their logic moves to `harness/__init__.py` (orchestration), `harness/lifecycle.py` (timeouts, process management), and `harness/verification.py` (verification — moved from Phase 1's standalone module).
- The `pipeline/` package retains `executor.py` as a thin wrapper that delegates to Harness, preserving the existing route handlers' API during transition.
- `pipeline/harness.py` is deleted once all references are updated to use the Harness class directly.
- Existing tests continue to pass via the same public interface.
- ContextAssembler moves to `harness/context.py` but keeps its current API.

**Files modified:**
- `src/superseded/harness/__init__.py` — new
- `src/superseded/harness/context.py` — moved from pipeline/context.py
- `src/superseded/harness/verification.py` — moved from verification.py
- `src/superseded/harness/checkpoint.py` — new
- `src/superseded/harness/lifecycle.py` — new
- `src/superseded/pipeline/harness.py` — refactored to use Harness
- `src/superseded/pipeline/executor.py` — refactored to use Harness

---

### Phase 3: Observability & Lifecycle

**Goal:** Production-grade health monitoring, graceful shutdown, resource limits, and crash recovery.

**Changes:**

1. **Health monitoring.** LifecycleManager tracks running agent subprocesses with a heartbeat:
   - Check subprocess is alive every 30 seconds
   - Check agent produced output in last N minutes (configurable)
   - If agent is alive but silent for >5 minutes, log warning and inject nudge into prompt: "You have been silent. Report progress or explain what you're working on."
   - If agent is dead, trigger checkpoint recovery

2. **Graceful shutdown.** On `SIGTERM`/`SIGINT`:
   - Send `SIGTERM` to all running agent subprocesses
   - Wait configurable grace period (default 30s)
   - Save checkpoints for all in-progress stages
   - Send `SIGKILL` to any still-running agents
   - Log shutdown summary

3. **Resource limits.** Per-stage configurable limits:
   ```yaml
   stages:
     build:
       max_tokens: 500000
       max_wall_time: 1800  # seconds
       max_cost: 5.00       # USD
   ```
   Limits enforced by LifecycleManager. Exceeded limits trigger stage failure with clear error.

4. **Persistent queue.** On startup, Harness checks DB for issues with `status = "in-progress"`. For each, check for checkpoint. If checkpoint exists and is valid, resume. If no checkpoint, mark as `failed` with reason: "Server restarted during execution. Retry to resume."

5. **Metrics endpoint.** `GET /metrics` exposes Prometheus-compatible metrics:
   - `superseded_stage_duration_seconds` (histogram, by stage)
   - `superseded_stage_result_total` (counter, by stage + result)
   - `superseded_agent_tokens_total` (counter, by agent)
   - `superseded_active_stages` (gauge)

**Files modified:**
- `src/superseded/harness/lifecycle.py` — health, shutdown, resource limits
- `src/superseded/harness/__init__.py` — persistent queue on startup
- `src/superseded/routes/metrics.py` — new, Prometheus endpoint
- `src/superseded/main.py` — signal handlers, startup recovery

---

### Phase 4: Granular HITL

**Goal:** Per-change approvals, multi-user support, richer notifications, bulk operations.

**Changes:**

1. **Per-file approval in REVIEW.** After REVIEW stage, show changed files with individual approve/reject. Users can approve most changes but reject specific files, sending targeted feedback: "Reject src/auth.py: use bcrypt, not sha256."

2. **Approval delegation.** Config-driven approver list:
   ```yaml
   approvers: ["alice", "bob", "charlie"]
   ```
   Any listed approver can approve/reject. UI shows who approved.

3. **Rich notifications.** Notification backends beyond ntfy.sh:
   ```yaml
   notifications:
     slack:
       webhook_url: "https://hooks.slack.com/..."
     email:
       smtp_host: "smtp.gmail.com"
       to: ["team@example.com"]
   ```
   Notification events: stage_complete, stage_failed, approval_required, questions_pending.

4. **Bulk operations.** Dashboard checkboxes for multi-select. Bulk actions: approve all selected, retry all failed, archive all done.

5. **Auto-advance config.** When verification passes and no approval required, automatically advance to next stage without manual click:
   ```yaml
   auto_advance: true
   ```

**Files modified:**
- `src/superseded/routes/web/issues.py` — per-file approval, bulk operations
- `src/superseded/notifications.py` — Slack, email, webhook backends
- `src/superseded/config.py` — approvers, auto_advance, notification backends
- `templates/issue_detail.html` — per-file approval UI
- `templates/dashboard.html` — bulk selection UI

---

## Cross-Cutting Concerns

### Backward Compatibility
Every phase preserves the existing ticket format, config format, and DB schema (via Alembic migrations). Existing `.superseded/` directories continue to work.

### Testing Strategy
Each phase includes tests:
- Phase 0: Config validation tests, init command tests
- Phase 1: VerificationEngine unit tests, artifact validation tests
- Phase 2: Harness integration tests, checkpoint/resume tests
- Phase 3: Lifecycle tests (shutdown, recovery), metrics endpoint tests
- Phase 4: Notification backend tests, bulk operation tests

### Documentation
Each phase updates:
- `docs/guides/user-guide.md` — user-facing changes
- `docs/architecture/` — architecture changes
- `docs/operations/troubleshooting.md` — new failure modes and recovery

---

## Success Criteria

After all four phases, Superseded should:

1. **Zero-config start:** `uv run superseded init && uv run superseded` gets a working harness
2. **Never lose work:** Any crash recovers from checkpoint within 2 minutes of data loss
3. **Catch agent mistakes:** Verification blocks 95%+ of incorrect outputs before they reach humans
4. **Show what's happening:** Real-time health, progress, and cost visibility without digging through logs
5. **Granular control:** Approve/reject at file level, delegate approvals, auto-advance when safe

---

## Resolved Design Questions

1. **Token budgeting** — Included in Phase 2 as part of the ContextAssembler move to `harness/context.py`. Add `tiktoken` dependency for accurate token counting. Configurable `max_context_tokens` per stage (default: model-specific, e.g. 100k for Claude). When context exceeds budget, oldest session turns are dropped first, then docs index is truncated. The 100k char cap is replaced by token-aware budgeting.

2. **Context compression for long sessions** — Included in Phase 2. Use a simple heuristic: session turns older than 5 attempts are replaced with a one-line summary (stage + result + key artifacts). No model call required — the summary is generated from structured metadata already in the DB. This keeps Phase 2 lightweight while solving context rot.

3. **Docker container resource limits** — Included in Phase 3 as part of LifecycleManager. Docker sandbox gets configurable `--memory`, `--cpus`, and `--pids-limit` flags. Defaults: 2GB memory, 2 CPUs, 256 pids. Host sandbox gets no OS-level limits (resource limits are enforced at the Harness level via max_tokens/max_wall_time).
