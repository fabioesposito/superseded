---
title: Agent Harness
category: architecture
summary: How the harness orchestrates multi-agent pipelines with feedback loops
tags: [harness, agents, orchestration]
date: 2026-04-19
---

# Agent Harness Architecture

The agent harness is the infrastructure layer that wraps AI agents and manages their lifecycle, context, tool access, verification, and safety. Superseded implements a harness as the central orchestration component.

## Harness Class

The `Harness` class (`src/superseded/harness/__init__.py`) is the core orchestrator. It composes:

- **ContextAssembler** — builds progressive context prompts from 10+ layers
- **VerificationEngine** — validates stage outputs against configurable criteria
- **CheckpointManager** — saves/loads stage progress for crash recovery
- **LifecycleManager** — health monitoring, graceful shutdown, resource limits
- **WorktreeManager** — isolated git worktrees per stage
- **AgentFactory** — creates agent adapters (Claude Code, OpenCode, Codex, Docker)
- **NotificationService** — sends alerts via ntfy.sh, Slack, or webhooks

## Stage Execution Flow

When `Harness.run_stage()` is called:

1. **Worktree creation** — BUILD/VERIFY/REVIEW stages run in isolated git worktrees
2. **Approval gate** — If `require_approval: true`, create `approval.md` and pause
3. **Context assembly** — Build prompt from AGENTS.md, docs, ticket, artifacts, rules, skill prompt
4. **Checkpoint resume** — If checkpoint exists, inject resume context into prompt
5. **Agent execution** — Stream agent output, persist events to DB
6. **Artifact extraction** — SPEC/PLAN stages write output to `*.md` files
7. **HITL detection** — Check for `questions.md` and `approval.md`
8. **Minimum output check** — Reject runs with < 50 chars output
9. **Verification** — Validate artifact sections, review findings, test results
10. **Checkpoint clear** — Clear checkpoint on success
11. **Notifications** — Send alerts on completion/failure
12. **State update** — Write issue status to DB

## Backward Compatibility

The `HarnessRunner` and `StageExecutor` classes in `pipeline/` are thin wrappers that delegate to `Harness`. They preserve the existing API surface while the logic lives in the harness package.

## Package Structure

```
src/superseded/harness/
  __init__.py          # Harness class
  context.py           # ContextAssembler
  verification.py      # VerificationEngine
  checkpoint.py        # CheckpointManager
  lifecycle.py         # LifecycleManager + HealthStatus + ResourceLimits
```
