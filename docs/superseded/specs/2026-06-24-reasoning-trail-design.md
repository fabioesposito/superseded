# Reasoning Trail Design

## Overview

Each AI-generated finding includes a short reasoning trail — 1–3 sentences explaining what evidence led the agent to flag the issue. This makes findings auditable: reviewers can see *why* the agent raised a concern, not just *what* it claimed.

The reasoning trail is:

- Requested from the agent via the review prompt
- Stored on the `Finding` model and persisted in SQLite
- Rendered as a collapsible `<details>` block in GitHub PR comments
- Preserved when findings are dismissed, so the memory system can learn from the reasoning behind rejected findings

## Data Model

### Finding (Pydantic)

`src/superseded/models.py`:

```python
class Finding(BaseModel):
    # ... existing fields ...
    reasoning: str = Field(default="")
```

The field defaults to empty string so findings from agents that don't provide reasoning still deserialize cleanly.

### Database Schema

`src/superseded/memory/store.py` — the `findings` table includes:

```sql
reasoning TEXT DEFAULT ''
```

For existing databases created before this feature, the `_migrate` method adds the column:

```python
if "reasoning" not in columns:
    await db.execute("ALTER TABLE findings ADD COLUMN reasoning TEXT DEFAULT ''")
```

The `record_finding` method accepts an optional `reasoning: str = ""` parameter and persists it alongside the other finding fields.

## Review Prompt

`src/superseded/review/prompts.py` instructs agents to include reasoning in two places:

1. **JSON format template** — the example schema includes:
   ```
   "reasoning": "1-3 sentences explaining what evidence led to this finding."
   ```

2. **Rules section** — a dedicated rule:
   ```
   - For each finding, briefly (1-3 sentences) explain what evidence led you to flag it
   ```

Agents are expected to return `reasoning` as a string field in each JSON finding object. If an agent omits it, the default empty string is used.

## Output Rendering

### GitHub PR Comments

`src/superseded/output/github_pr.py` renders reasoning as a collapsible HTML block:

```python
if f.reasoning:
    body_text += (
        f"<details><summary>Reasoning</summary>\n\n"
        f"{_escape_reasoning(f.reasoning)}\n\n</details>\n\n"
    )
```

The reasoning text is HTML-escaped (`<` → `&lt;`, `>` → `&gt;`) to prevent injection and ensure the `<details>` tag structure is preserved. When reasoning is empty, no details block is rendered.

### Local Output

JSON and markdown output formats include the `reasoning` field as-is. No special rendering is applied for local output — the raw text is included in the finding object.

## Dismissed Findings Learn-Back

When a human dismisses a finding via a GitHub reaction or the `feedback` CLI command, the finding (including its reasoning) is preserved in the database with `dismissed = TRUE`.

On subsequent reviews, dismissed findings are loaded via `MemoryStore.get_dismissed_findings()` and injected into the prompt as past feedback:

```
### Past Feedback (findings dismissed by humans — avoid similar)
```

The reasoning trail is included in this context, so the agent can understand *why* a human rejected a similar finding and avoid repeating the same mistake. This is the primary feedback loop that prevents the tool from re-raising issues that humans have already evaluated and dismissed.
