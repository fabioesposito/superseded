---
title: Pipeline Recovery Paths — Design
category: adrs
summary: Pipeline Recovery Paths — Design
tags: []
date: 2026-04-14
---

# Pipeline Recovery Paths — Design

## Problem

When agents fail or encounter ambiguity, the current feedback loop is one-directional:
- Agent fails → error captured → retry with error context → fail again → PAUSED
- No way for agent to ask questions
- No way for user to edit ticket mid-pipeline

## Scope

Two recovery mechanisms:

| Feature | Trigger | Resume action |
|---------|---------|---------------|
| Agent-to-human questions | Agent writes `questions.md` | User answers, stage re-runs with answers |
| Inline ticket edit | User clicks "Edit Ticket" on paused issue | User edits body, stage re-runs with new ticket |

## Design

### Pause Reasons

Add `PauseReason` enum to distinguish paused states:

```python
class PauseReason(StrEnum):
    RETRIES_EXHAUSTED = "retries-exhausted"
    AWAITING_INPUT = "awaiting-input"
    USER_EDIT = "user-edit"
```

Add `pause_reason: str = ""` to `Issue` model and DB schema.

### Agent-to-Human Questions

#### Prompt Change

Add to all stage prompts in `prompts.py`:

```
If requirements are ambiguous or you need clarification:
1. Write questions to `questions.md` in the artifacts directory
2. Format each as: ## Q: [question]
3. Exit with code 0 (success) — the pipeline will pause for human input
4. Do NOT guess or make assumptions
```

#### Detection

In `harness.py:run_stage_with_retries()`, after agent completes with exit_code 0:
- Check if `{artifacts_path}/questions.md` exists
- If yes, return `StageResult(passed=False, error="awaiting-input")`

In `executor.py:_run_single_repo()`, when result is failed:
- Check artifacts for `questions.md`
- Set `pause_reason = "awaiting-input"` if found
- Otherwise `pause_reason = "retries-exhausted"`

#### UI

In `_actions.html`, when paused with `awaiting-input`:
- Render questions from `questions.md` (parsed as markdown)
- Show text input for each question
- "Submit Answers & Resume" button

#### Resume

New endpoint `POST /issues/{id}/answer-questions`:
1. Receives answers as form data
2. Writes `answers.md` to artifacts
3. Re-runs current stage

New context layer in `ContextAssembler`:
```python
def _build_answers_layer(self, artifacts_path: str) -> str | None:
    answers_file = Path(artifacts_path) / "answers.md"
    if answers_file.exists():
        return f"## Human Answers\n\n{answers_file.read_text()}"
    return None
```

### Inline Ticket Edit + Resume

#### UI

In `issue_detail.html`, when issue is `PAUSED`:
- Expandable "Edit Ticket" section
- Textarea with current ticket body (not frontmatter)
- "Save & Resume" / "Cancel" buttons

#### Backend

New endpoint `POST /issues/{id}/update-body`:
1. Receives new body text
2. Reads current ticket file, parses frontmatter
3. Replaces body, preserves frontmatter
4. Writes back to disk
5. Updates SQLite
6. Re-runs current stage

No prompt change needed — `ContextAssembler._build_issue_layer()` reads ticket fresh each time.

### Dashboard Differentiation

In `_dashboard_table.html`, when status is `paused`:
- `retries-exhausted` — coral badge "Retries Exhausted"
- `awaiting-input` — sand badge "Awaiting Input"
- `user-edit` — shell badge "User Editing"

## Files to Modify

| File | Changes |
|------|---------|
| `src/superseded/models.py` | Add `PauseReason` enum, `pause_reason` field |
| `src/superseded/db.py` | Add `pause_reason` column, getter/setter |
| `src/superseded/pipeline/prompts.py` | Add questions instruction to all stage prompts |
| `src/superseded/pipeline/harness.py` | Detect `questions.md` after agent runs |
| `src/superseded/pipeline/executor.py` | Set `pause_reason` on failure |
| `src/superseded/pipeline/context.py` | Add `_build_answers_layer()` |
| `src/superseded/routes/issues.py` | Add `POST /{id}/answer-questions` and `POST /{id}/update-body` |
| `src/superseded/routes/pipeline.py` | Wire pause_reason through advance/retry |
| `templates/_actions.html` | Conditional UI based on pause reason |
| `templates/_dashboard_table.html` | Pause reason badges |
| `templates/issue_detail.html` | Edit ticket section |

## Verification

1. Create issue, run build stage
2. Agent should write `questions.md` if ambiguous
3. Issue should show "Awaiting Input" in dashboard
4. User answers questions, stage re-runs
5. On PAUSED issue, click "Edit Ticket"
6. Edit body, click "Save & Resume"
7. Stage re-runs with updated ticket
