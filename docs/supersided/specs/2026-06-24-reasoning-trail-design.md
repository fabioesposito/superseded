# Reasoning Trail — Design

Date: 2026-06-24
Status: Approved
Scope: Feature #3 from the CodeRabbit article — the output-side grounded-review upgrade. This spec covers (a) persisting each finding's reasoning rationale to the memory store for future-run learn-back and (b) rendering the reasoning trail in the current run's output (markdown + GitHub PR comments).

Companion spec: `2026-06-24-grounded-review-context-design.md` (input side — features #1 and #2).

## Motivation

Superseded currently persists each finding's title, description, severity, file, and line to the SQLite memory store (`memory/store.py`) for the dismissed-feedback loop (`cli.py:151-164`). When humans dismiss a finding, the *next* run's prompt includes it in `memory_context` as a single-line bullet (`cli.py:35-43`) so the agent can avoid repeating the same mistake. But the store does not record *why the agent flagged it in the first place* — the reasoning rationale is discarded at parse time (`engine.py:62-68`).

This means the dismissed-feedback loop can only say "don't report X" but cannot say "don't report X for this reason." An agent might re-raise the same finding next time, or re-raise the correct concern but with an overbroad description, because it has no signal about what the human actually objected to.

CodeRabbit's lesson: "track an agent's reasoning trail." Persisting the per-finding rationale lets the feedback loop learn more precisely and lets human reviewers see *why* each finding was flagged without leaving the PR review.

## Design choices (decided)

| Decision | Choice | Rejected alternative | Rationale |
|---|---|---|---|
| Field on `Finding` | `reasoning: str = Field(default="")` | Required non-empty string | Backwards compatible: existing agent JSON outputs that don't emit `reasoning` still parse; default-empty keeps the schema lenient |
| Factor into `id` hash | No — reasoning stays out of the SHA-256 | Include it | `id` reflects *what* was found (pass/file/line/title), not *why*. Two agents flagging the same code for different reasons should get the same `id` if their code content is identical |
| Memory-context cap | 300 chars per finding | No cap; unlimited; shared token budget | 1-3 sentences fits within 300 chars; prevents dismissed-findings block from bloating the prompt as memory accumulates |
| Raw storage cap | None | Capped at 300 chars | Humans see full reasoning in the current run's output; only the *learn-back* rendering truncates |
| Merger dedup by rationale | Out of scope (future spec) | Include in v1 | Needs an embedding dep or a per-merge LLM call — not appropriate for a v1 CLI tool |
| `format_table` rendering | Skip | Include | Table is a glance view; reasoning is multi-sentence; rendering would break column alignment |

## Architecture

### Where reasoning lives

Reasoning enters the system via the agent's JSON output (`engine.py:62`), persists in the `findings` SQLite table (`memory/store.py`), is abbreviated when fed back as `memory_context` (`cli.py:35-43`), and renders in markdown/GitHub output (`output/markdown.py`, `output/github_pr.py`). No new packages, no new modules — surgical edits to four existing files.

```
Agent CLI output (JSON)
  └─ engine.py:62 — parse_output returns list[dict] including "reasoning"
       └─ models.py — Finding.reasoning: str persisted on each Finding
            ├─ cli.py:_persist_findings — stores reasoning to SQLite
            │    └─ memory/store.py — findings table, reasoning column
            ├─ cli.py:format_memory_context — abbreviated rationale in next-run prompt
            └─ output/markdown.py / output/github_pr.py — <details> block in output
```

## Schema — `models.py`

```python
class Finding(BaseModel):
    pass_name: PassName
    severity: Severity
    file: str
    line: int
    end_line: int
    title: str
    description: str
    suggestion: str
    confidence: Confidence = "high"
    reasoning: str = Field(default="")    # <-- NEW
    id: str = Field(default="")
```

`model_post_init` (`models.py:25`) is **untouched** — `reasoning` does not factor into the `id` hash. The hash remains:
```python
raw = f"{self.pass_name}-{self.file}-{self.line}-{self.title}"
```
This ensures two findings with identical code content but different rationale (e.g. two agents flagging the same line for different reasons) share the same `id` — the hash reflects *what* was found, not *why*.

## Prompt — `prompts.py`

### JSON_FORMAT_INSTRUCTIONS (prompts.py:26-44)

The example array gains the `reasoning` field:
```jsonc
[
  {
    "severity": "critical|important|suggestion|nit",
    "confidence": "high|medium|low",
    "file": "path/to/file.py",
    "line": 42,
    "end_line": 45,
    "title": "Short description",
    "description": "Detailed explanation of the issue",
    "suggestion": "Code fix or suggestion",
    "reasoning": "1-3 sentences explaining what evidence led to this finding."
  }
]
```

### Rules block (prompts.py:64-69)

A new rule is appended to the existing list:
```
- For each finding, briefly (1-3 sentences) explain what evidence led you to flag it
```
No new section heading — the flat-list style is preserved. Agent CLIs (which follow the schema in the prompt) now emit reasoning without code changes to `agents/*.py`.

## Memory store — `memory/store.py`

### Schema migration

`SCHEMA` (store.py:9) gains the column definition in the `CREATE TABLE` block:
```sql
CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    repo TEXT,
    pass TEXT,
    severity TEXT,
    file TEXT,
    line INTEGER,
    reasoning TEXT DEFAULT ''    -- <-- NEW (positional: after line, before title)
    title TEXT,
    description TEXT,
    dismissed BOOLEAN DEFAULT FALSE,
    comment_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```
New databases created with the updated `SCHEMA` include the column from the start.

Existing databases (created before this change) are handled by `_migrate` (store.py:43). A new migration block is added:
```python
if "reasoning" not in columns:
    await db.execute("ALTER TABLE findings ADD COLUMN reasoning TEXT DEFAULT ''")
```
Mirrors the existing `comment_id` migration (store.py:46-47).

### record_finding (store.py:49-65)

`reasoning` is added as a new positional arg (with a default for backwards compatibility):
```python
async def record_finding(
    self,
    finding_id: str,
    repo: str,
    pass_name: str,
    severity: str,
    file: str,
    line: int,
    title: str,
    description: str,
    reasoning: str = "",    # <-- NEW
) -> None:
```
The `INSERT OR IGNORE` columns and values gain `reasoning` / `reasoning`.

### get_dismissed_findings (store.py:106-114)

Unchanged — returns full rows (`SELECT *`) which now include the `reasoning` column automatically.

## Memory learn-back — `cli.py`

### _persist_findings (cli.py:151-164)

Passes `reasoning=f.reasoning` to `store.record_finding()`.

### format_memory_context (cli.py:35-43)

When dismissed findings have non-empty `reasoning`, the renderer produces a two-line form:
```
- Performance pass: "N+1 query in loop" — dismissed by human review.
  Rationale then was: "Loops over 1000 rows; will hit DB N times per request."
```
When `reasoning` is empty (old findings that were persisted before this change), the original single-line form is kept:
```
- Performance pass: "N+1 query in loop" — dismissed by human review.
```

Per-finding rationale is truncated to **300 chars** with a `…` tail. This bounds the growth of the `memory_context` block as dismissed findings accumulate across multiple PRs. Raw storage is uncapped — the full trail is available in the current run's output and in the SQLite database.

## Output rendering

### format_json (json_out.py:8-10)

Already gets reasoning for free via `model_dump()` — the new field surfaces automatically. No edit needed.

### format_markdown (markdown.py:25-32)

When `f.reasoning` is non-empty, a collapsible block is inserted after the description:
```markdown
### {f.title}
**{f.file}:{f.line}-{f.end_line}** ({f.pass_name})

{f.description}

<details><summary>Reasoning</summary>

{f.reasoning}

</details>

**Suggestion:** {f.suggestion}
```
When `reasoning` is empty, no `<details>` block renders — no visual noise for findings agents chose not to explain.

### format_table (table.py:13)

Skip — too narrow for a multi-sentence trail; would break column alignment.

### post_review_to_pr (github_pr.py:11-23)

The comment body gains the same `<details>` block:
```python
body = f"**[{f.severity.upper()}] {f.title}** ({f.pass_name})\n\n"
body += f"{f.description}\n\n"
if f.reasoning:
    body += "<details><summary>Reasoning</summary>\n\n"
    body += f"{f.reasoning}\n\n"
    body += "</details>\n\n"
body += f"**Suggestion:** {f.suggestion}"
```
No-op when reasoning is empty.

## Known bug (parked separately)

`github_pr.py:74` contains:
```python
except subprocess.CalledProcessError, FileNotFoundError:
```
The Python 2 comma form silently treats `FileNotFoundError` as the *exception variable name*, not a second exception type. This means `FileNotFoundError` raised by `subprocess.run` is no longer caught by `current_repo()`. The correct form is:
```python
except (subprocess.CalledProcessError, FileNotFoundError):
```
This is the same bug class as the recently-fixed `diff.py:53` commit. Not part of #3 — to be fixed as a separate one-liner commit before starting #3 implementation, or folded into the implementation plan.

## Testing plan

### tests/test_models.py (extend)

- `reasoning` defaults to `""` when omitted from `Finding(...)`.
- Roundtrip via `model_dump()` → `Finding(**dict)` preserves `reasoning`.
- `id` is unchanged regardless of whether reasoning is provided — same pass/file/line/title → same `id`.

### tests/test_prompts.py (extend — also from context spec)

- `JSON_FORMAT_INSTRUCTIONS` string contains `"reasoning"`.
- Rules block mentions the 1-3 sentence guidance.
- When reasoning is absent from agent output, existing prompt shape is unchanged (regression: full old prompt string is still equal when new kwargs are `None`).

### tests/test_memory_store.py (new or extend)

- **Migration**: create a DB using the old `SCHEMA` (without `reasoning`), run `_migrate`, assert `reasoning` column exists and defaults to `''`.
- **Roundtrip**: `record_finding(reasoning="...")` → `get_dismissed_findings` → assert `reasoning` is present in returned dict.
- **Backwards compat**: `record_finding()` called without `reasoning` → `get_dismissed_findings` → assert `reasoning` is `''`.

### tests/test_cli.py (extend)

- `format_memory_context` with non-empty reasoning renders the two-line form including the rationale.
- `format_memory_context` with empty reasoning renders the original single-line form.
- `format_memory_context` with reasoning exceeding 300 chars renders truncated tail with `… ({N} chars omitted)`.
- `_persist_findings` passes `reasoning=f.reasoning` to `record_finding` (mock `MemoryStore`, assert call args).

### tests/test_output_markdown.py (new or extend)

- Reasoning non-empty → `<details>` block present and contains the rationale text.
- Reasoning empty → no `<details>` block rendered.

### tests/test_output_github_pr.py (new or extend)

- Comment body includes `<details>` block when reasoning non-empty.
- Comment body excludes `<details>` block when reasoning empty.

## Out of scope

- Merger dedup by semantic rationale (needs embeddings — future spec).
- Capping reasoning on the `Finding` model itself (only the memory_context renderer caps at 300 chars; raw output is uncapped).
- Adding reasoning to `format_table`.
- Any new top-level package — #3 is surgical edits only.
- Agent code changes (`agents/*.py`) — agents follow the JSON schema in the prompt; no agent-specific code needed.