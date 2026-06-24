---
title: Pipeline Engine
category: architecture
summary: Pipeline stage flow, retry logic, and worktree isolation
tags: [pipeline, stages, worktree]
date: 2026-04-19
---

# Pipeline Architecture

The pipeline orchestrates ticket progression through six stages: Spec → Plan → Build → Verify → Review → Ship.

## Components

### HarnessRunner (`pipeline/harness.py`)
Backward-compatible wrapper around `Harness`. Delegates all stage execution, context assembly, and verification to the harness package.

### StageExecutor (`pipeline/executor.py`)
Backward-compatible wrapper around `Harness`. Handles the route-facing API: `run_stage(issue, stage, config)`.

### PipelineEventManager (`pipeline/events.py`)
Pub/sub event system for SSE streaming. Each issue gets a queue. Events are published as agents produce output and persisted to the DB.

### ContextAssembler (`harness/context.py`)
Builds progressive context prompts from layers:
1. AGENTS.md (repository guide)
2. Docs index (categorized docs)
3. Issue ticket
4. Target repo context (multi-repo)
5. Previous stage artifacts
6. Human answers
7. Session history
8. Project rules
9. Skill prompt (stage-specific instructions)
10. Error context (retry feedback)

### VerificationEngine (`harness/verification.py`)
Validates stage outputs:
- **SPEC/PLAN**: Checks required sections exist in artifact markdown
- **REVIEW**: Parses severity headings (Critical/Important/Nit/FYI), enforces thresholds
- **VERIFY**: Parses test output (pytest/jest/go test) for failures

## Stage Definitions

Each stage maps to a vendored skill:
- Spec → `spec-driven-development`
- Plan → `planning-and-task-breakdown`
- Build → `incremental-implementation`
- Verify → `test-driven-development`
- Review → `code-review-and-quality`
- Ship → `git-workflow-and-versioning`

## Multi-Repo Fan-Out

When a ticket targets multiple repos (`repos: [frontend, backend]`), SPEC/PLAN run once. BUILD/VERIFY/REVIEW fan out per target repo with isolated worktrees. SHIP creates a PR per repo.
