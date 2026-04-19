# Superseded Harness Design

> Close the five critical gaps between Superseded's current pipeline and the OpenAI Codex harness pattern: feedback loops, execution plans, progressive context, worktree isolation, and quality enforcement.

## Problem Statement

Superseded's current pipeline is fire-and-forget. Each stage runs an agent once, records pass/fail, and advances. This makes it a linear orchestrator, not a harness. The OpenAI harness article identifies five patterns that make agent-driven development work at scale:

1. **Agents iterate, not just execute** — failure triggers re-prompting with error context
2. **Plans are first-class artifacts** — structured, tracked, and consumed by downstream stages
3. **Context is progressive, not monolithic** — agents get a map, not a 1,000-page manual
4. **Work happens in isolation** — git worktrees per issue, merge on success, discard on failure
5. **Quality compounds** — review findings loop back, golden rules are enforced on every run

Without these, the pipeline cannot reliably drive an issue from spec to ship. Agents need loops, not just prompts.

## Design Decisions

### D1: HarnessRunner replaces fire-and-forget stage execution

The current `PipelineEngine.run_stage` runs once and returns. A new `HarnessRunner` class wraps stage execution with retry logic. On failure, it re-prompts the agent with the error output from the previous attempt. On final failure (after `max_retries`), it marks the issue as `PAUSED` and surfaces the error chain in the UI.

**Why:** The OpenAI article explicitly describes agents working in loops — "respond to any human or agent given feedback, and iterate in a loop until all agent reviewers are satisfied." This is the single highest-leverage change.

**Config:** `max_retries` per stage (default: 3 for Build/Verify, 1 for Ship). Configurable in `.superseded/config.yaml`.

### D2: Execution plans are structured, versioned artifacts

The Plan stage writes a structured `plan.md` to `.superseded/artifacts/{issue_id}/plan.md`. The format includes:

```markdown
# Plan: {issue title}

## Context
{issue description}

## Tasks
### Task 1: {title}
- **Description:** {one paragraph}
- **Acceptance criteria:** {testable conditions}
- **Verification:** {command to run}
- **Dependencies:** {task numbers}
- **Scope:** {Small/Medium/Large}

### Task 2: ...
```

Build, Verify, and Review stages read this plan as primary input. The `HarnessRunner` tracks which task in the plan each iteration is working on.

**Why:** Plans as ephemeral output means each stage operates blindly. Structured plans give agents continuity and allow the harness to track progress across iterations.

### D3: ContextAssembler builds progressive context

A new `ContextAssembler` class replaces the current `get_prompt_for_stage(stage)` call. It builds context in layers:

1. **Repository map** — `AGENTS.md` (if present, used as table of contents, not full instructions)
2. **Docs index** — list of files in `docs/` with one-line summaries
3. **Issue ticket** — the full markdown content of the issue
4. **Previous artifacts** — outputs from completed stages (spec.md, plan.md, etc.)
5. **Golden rules** — `.superseded/rules.md` content (if present)
6. **Skill prompt** — the stage-specific skill from agent-skills or built-in
7. **Error context** — on retry, the previous attempt's error output

Each layer is optional. The assembler only includes what exists. Layers are separated by clear delimiters so the agent can navigate them.

**Why:** The OpenAI article explicitly warns against the "one big AGENTS.md" anti-pattern: "context is a scarce resource" and "too much guidance becomes non-guidance." Progressive disclosure gives agents what they need without overwhelming them.

### D4: Git worktree isolation per issue

Before the Build stage starts, the harness:

1. Creates a git worktree: `git worktree add .superseded/worktrees/{issue_id} -b issue/{issue_id}`
2. Sets `AgentContext.worktree_path` to the worktree directory
3. Agent adapters receive the worktree path as their `cwd`
4. On success, changes are committed in the worktree branch and merged back via PR
5. On failure or pause, the worktree is preserved for inspection
6. Cleanup: `git worktree remove` when the issue reaches DONE or is abandoned

The `WorktreeManager` class handles creation, status tracking, and cleanup.

**Why:** The OpenAI article describes agents "booting one instance per change" in isolated worktrees. Without isolation, concurrent issues stomp on each other, and a failed agent leaves the working directory in an unknown state.

**Edge case:** If the repo has uncommitted changes, the harness stashes them before creating the worktree and restores after.

### D5: Quality enforcement with golden rules and review feedback loops

Two mechanisms:

**Golden rules** — A `.superseded/rules.md` file that gets injected into every agent prompt. Contains project-specific invariants like:

```markdown
- Parse data shapes at the boundary, not inside business logic
- Run the full test suite before committing
- No manually-written code for data transformations — use the schema system
- All new files must have corresponding tests
```

The `ContextAssembler` includes this as layer 5 (before the skill prompt).

**Review feedback loop** — After the Review stage, if the result contains critical or important findings, the harness loops back to Build instead of advancing to Ship. The review findings are injected as error context for the next Build iteration. This continues until Review passes or `max_retries` is exhausted.

**Why:** The OpenAI article describes "golden principles" that are "opinionated, mechanical rules that keep the codebase legible and consistent for future agent runs." They also describe agent review loops where "respond to any human or agent given feedback, and iterate in a loop until all agent reviewers are satisfied."

## Architecture Changes

### New Classes

```
src/superseded/pipeline/
  harness.py         # HarnessRunner — retry loop, stage orchestration
  context.py          # ContextAssembler — progressive context building
  worktree.py         # WorktreeManager — git worktree lifecycle
  plan.py             # Plan parser — read/write structured plans

src/superseded/models.py (additions)
  HarnessConfig       # max_retries, retryable_stages, etc.
  HarnessIteration     # single attempt within a stage
  PlanTask             # structured task from plan.md
```

### Modified Classes

```
PipelineEngine.run_stage() → HarnessRunner.run_stage_with_retries()
  - Wraps run_stage with retry logic
  - Injects error context on retry
  - Pauses on final failure

AgentContext (additions)
  + worktree_path: str   # path to isolated worktree
  + iteration: int      # which retry attempt (0-indexed)
  + previous_errors: list[str]  # error messages from prior attempts

PipelineEngine.run_stage() (modify)
  - Accept AgentContext with worktree_path
  - Use context.repo_path OR context.worktree_path as cwd

routes/pipeline.py (modify)
  - advance_issue calls HarnessRunner instead of just updating status
  - Added endpoint for viewing harness iteration history
```

### Data Flow

```
Issue created
  → HarnessRunner.run_stage(SPEC)
    → ContextAssembler.build(SPEC, issue, artifacts=[], previous_errors=[])
    → Agent runs in main repo (spec doesn't need worktree)
    → On failure: retry with error context (up to max_retries)
    → On final failure: pause issue, surface errors in UI
  → HarnessRunner.run_stage(PLAN)
    → ContextAssembler.build(PLAN, issue, artifacts=[spec.md], previous_errors=[])
    → Agent produces plan.md in .superseded/artifacts/{id}/plan.md
  → HarnessRunner.run_stage(BUILD)
    → WorktreeManager.create(issue) → isolated worktree
    → ContextAssembler.build(BUILD, issue, artifacts=[spec.md, plan.md], previous_errors=[])
    → Agent runs in worktree
    → On success: commit in worktree, record for merge
  → HarnessRunner.run_stage(VERIFY)
    → ContextAssembler.build(VERIFY, issue, artifacts=[spec.md, plan.md], previous_errors=[])
    → Agent runs in worktree
  → HarnessRunner.run_stage(REVIEW)
    → ContextAssembler.build(REVIEW, issue, artifacts=[all prior], previous_errors=[])
    → Agent runs in worktree
    → If critical/important findings: loop back to BUILD with review feedback
  → HarnessRunner.run_stage(SHIP)
    → Merge worktree branch, create PR
    → WorktreeManager.cleanup(issue)
```

### New File: .superseded/rules.md

Optional golden rules file. If present, injected into every prompt. Template:

```markdown
# Project Rules

Agents must follow these rules on every run. These are non-negotiable invariants.

- {rule 1}
- {rule 2}
- ...
```

### New Directory: docs/

The OpenAI article's progressive disclosure model expects a structured docs directory:

```
docs/
├── ARCHITECTURE.md     # high-level system map
├── DESIGN.md           # design decisions
├── PRODUCT_SENSE.md    # product principles  
└── ... (existing plans/)
```

The `ContextAssembler` includes a docs index (filenames + first-line summaries) rather than full content.

## Minimal Viable Harness Sequence

After this change, a typical issue flow becomes:

1. Human creates ticket → issue in `NEW` status
2. Advance → `HarnessRunner.run_stage(SPEC)` up to 3 retries
3. Success → spec.md saved, auto-advance to PLAN
4. `HarnessRunner.run_stage(PLAN)` → plan.md saved
5. `HarnessRunner.run_stage(BUILD)` → worktree created, agent works in isolation
6. `HarnessRunner.run_stage(VERIFY)` → tests run in worktree
7. `HarnessRunner.run_stage(REVIEW)` → if findings, loop back to BUILD with feedback
8. `HarnessRunner.run_stage(SHIP)` → merge, PR, cleanup
9. Any stage final-failure → issue pauses, errors in UI, human decides: retry/skip/override

## What This Does NOT Include (future work)

- **Session/event persistence** — no resumable sessions (Approach C)
- **Mid-execution steering** — no UI to inject context mid-run
- **Container management** — stays local-first, no sandboxed containers
- **MCP server integration** — no external tool providers
- **Doc gardening agent** — no recurring cleanup tasks (could be a future pipeline stage)
- **Observability stack** — no log/metrics integration for agents

## Impact on Existing Code

| Module | Change |
|--------|--------|
| `models.py` | Add `HarnessConfig`, `HarnessIteration`, `PlanTask`; add fields to `AgentContext` |
| `pipeline/engine.py` | Minimal change — `run_stage` accepts `worktree_path` from context |
| `pipeline/prompts.py` | Replaced by `pipeline/context.py` (ContextAssembler) — prompts are one layer |
| `pipeline/stages.py` | No change — stage definitions unchanged |
| `agents/base.py` | No change — protocol unchanged |
| `agents/claude_code.py` | Use `context.worktree_path or context.repo_path` as cwd |
| `agents/opencode.py` | Use `context.worktree_path or context.repo_path` as cwd |
| `routes/pipeline.py` | Replace simple advance/retry with HarnessRunner calls |
| `routes/dashboard.py` | Show harness iteration history |
| `templates/issue_detail.html` | Add iteration history, error chain display |
| New: `pipeline/harness.py` | HarnessRunner — retry loop orchestration |
| New: `pipeline/context.py` | ContextAssembler — progressive context building |
| New: `pipeline/worktree.py` | WorktreeManager — git worktree lifecycle |
| New: `pipeline/plan.py` | Plan parser — read/write structured plans |
| Config: `.superseded/config.yaml` | Add `max_retries`, `retryable_stages` |
| New: `.superseded/rules.md` | Optional golden rules template |