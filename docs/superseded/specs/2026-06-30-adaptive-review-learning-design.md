# Adaptive Review Learning — Design

**Date:** 2026-06-30
**Status:** Draft (pending user review)
**Topic:** Adaptive review criteria engine that learns from feedback outcomes to adjust what gets flagged and how aggressively.

## Goal

Track dismissal/acceptance patterns across reviews and use them to shape future
reviews. Two mechanisms:

1. **Statistical thresholds** — pre-computed per-pass/severity/file_pattern
   dismissal rates, queried inline every review and injected as prompt guidance.
2. **LLM-driven pattern reflection** — when enough new feedback accumulates,
   run a meta-reflection pass that infers general team preferences from
   dismissal clusters; store as natural-language rules in memory.

Both feed into a new `### Learned Review Guidelines` prompt section. The system
learns without extra CLI commands — everything runs inline.

## Scope & non-goals

**In scope:**

- Pre-computed stats table refreshed after each review cycle (`_refresh`).
- Stats queried inline every review; produces guidance text like "prefer
  'important' or higher for style pass in test files."
- LLM reflection triggered automatically when unprocessed feedback ≥ threshold
  (default 5). Uses the already-configured review agent (same CLI, different
  prompt).
- Learned rules stored in SQLite with confidence scores. Top-N rules injected
  into each review prompt.
- Config toggle (`learned_review`) and tunable thresholds.
- Both CLI and server-mode paths. Same `MemoryStore` schema; separate DB files
  keep state independent as with progressive review.

**Out of scope:**

- Codebase-mined patterns (extracting conventions from the code itself, not from
  feedback). Valuable but separate — requires tree-sitter/AST work.
- Cross-repo rule sharing or centralized learned-rule storage.
- Manual rule editing UI or feedback subcommand for rules (future: users can
  edit `.superseded.yaml` conventions; learned rules are read-only).
- Auto-suppression of findings. Rules are prompt-only guidance; the AI decides
  whether to follow them.
- Dedicated reflection agent — reuses the existing review agent for simplicity.
- Dismissal of learned rules via feedback (future: decrement confidence).

## Design decisions

| Decision | Choice | Rationale |
|---|---|---|
| Stats location | Pre-computed `review_stats` table, refreshed after each cycle | `findings` table can grow large; live aggregation per review adds latency to an already-parallel fan-out near timeout |
| Reflection trigger | Inline, threshold-gated on unprocessed feedback count | User chose "every review"; but reflection itself is an LLM call — throttled to run only when 5+ new feedback items exist |
| Reflection agent | Same agent as review (claude-code/opencode/codex) | Already authenticated, configured. A simple JSON-output prompt works for all agents. No new agent class needed |
| Rule format | Natural-language sentences in `learned_rules` table | Prompt-only injection; the AI reads the rules and decides. No structured rule engine needed |
| Prompt placement | Between Specs and PR Description | Conventions/Specs are authoritative team intent; learned rules are inferred team intent (lower priority) |
| File patterns | Heuristic detection via file path (`test/`, `migrations/`, `*.yaml`) | Covers 80% of cases without language-specific parsing; can be extended later |
| CLI flags | None in v1 | Matches `static_analysis`/`usage_retrieval` pattern; config-only toggle |

## Data model

Three new tables in the shared `MemoryStore` schema. Added to `SCHEMA` and
`_migrate()` alongside existing tables.

```sql
CREATE TABLE IF NOT EXISTS review_stats (
    repo         TEXT    NOT NULL,
    pass         TEXT    NOT NULL,
    severity     TEXT    NOT NULL,
    file_pattern TEXT    NOT NULL DEFAULT '*',
    total        INTEGER NOT NULL DEFAULT 0,
    accepted     INTEGER NOT NULL DEFAULT 0,
    dismissed    INTEGER NOT NULL DEFAULT 0,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (repo, pass, severity, file_pattern)
);

CREATE TABLE IF NOT EXISTS learned_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    repo            TEXT    NOT NULL,
    rule_text       TEXT    NOT NULL,
    evidence_count  INTEGER NOT NULL DEFAULT 0,
    confidence      REAL    NOT NULL DEFAULT 1.0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_applied_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reflection_state (
    repo               TEXT    NOT NULL,
    last_feedback_id   INTEGER NOT NULL,
    last_reflection_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (repo)
);
```

**`file_pattern` values:** `*` (default), `test`, `migration`, `config`.
Classification is heuristic:

- `test`: paths matching `test/`, `tests/`, `*_test.*`, `test_*`, `__test__/`
- `migration`: paths matching `migrations/`, `*/migrations/`
- `config`: `.yaml`, `.yml`, `.toml`, `.json`, `Dockerfile*`
- `*`: everything else

**`confidence`:** 1.0 on creation. Future: decremented when a learned rule
itself appears in dismissed finding feedback. Rules with confidence < 0.3 are
excluded from prompts.

### New `MemoryStore` methods

```python
async def get_learned_rules(self, repo: str, limit: int = 5) -> list[dict]:
    """Return top rules for repo, sorted by confidence desc, created_at desc.
    Also updates last_applied_at for the returned rules."""
    # SELECT * FROM learned_rules WHERE repo = ? AND confidence >= 0.3
    # ORDER BY confidence DESC, created_at DESC LIMIT ?
    # UPDATE learned_rules SET last_applied_at = CURRENT_TIMESTAMP WHERE id IN (...)

async def get_reflection_state(self, repo: str) -> int:
    """Return last_feedback_id or 0 if no row exists."""

async def set_reflection_state(self, repo: str, last_feedback_id: int) -> None:
    """INSERT OR REPLACE INTO reflection_state (repo, last_feedback_id) VALUES (?, ?)"""
```

## New package: `src/superseded/audit/`

Placed outside `review/` because learning operates at a meta-level — it observes
review outcomes and generates input for future reviews, distinct from pass
orchestration.

```
src/superseded/audit/
    __init__.py
    stats.py         # StatsAggregator
    reflector.py     # PatternReflector
    guidelines.py    # assemble_learned_context()
```

## Module 1 — `audit/stats.py`

### `StatsAggregator`

```python
class StatsAggregator:
    def __init__(self, store: MemoryStore) -> None: ...

    async def get_stats_context(self, repo: str) -> str | None:
        """Return stats-formatted prompt block or None if no data."""
        ...

    async def _refresh(self, repo: str) -> None:
        """Upsert review_stats from findings+feedback for this repo."""
        ...
```

### `_refresh(repo)`

Upsert `review_stats` from `findings` joined with `feedback`. Only findings with
at least one feedback row are counted (un-judged findings don't contribute to
ratios). The file_pattern CASE expression classifies each finding's file path
using the heuristics above. Implementation note: the CASE appears in both SELECT
and GROUP BY — use a helper function `_classify_file_pattern(file: str) -> str`
called in both positions to avoid duplication.

```sql
INSERT INTO review_stats (repo, pass, severity, file_pattern, total, accepted, dismissed)
SELECT f.repo, f.pass, f.severity,
       _classify_file_pattern(f.file) AS file_pattern,
       COUNT(*) AS total,
       COUNT(*) FILTER (WHERE fb.action = 'helpful') AS accepted,
       COUNT(*) FILTER (WHERE fb.action = 'dismiss') AS dismissed
FROM findings f
JOIN feedback fb ON fb.finding_id = f.id
WHERE f.repo = ?
GROUP BY f.repo, f.pass, f.severity, _classify_file_pattern(f.file)
ON CONFLICT(repo, pass, severity, file_pattern) DO UPDATE SET
    total = excluded.total,
    accepted = excluded.accepted,
    dismissed = excluded.dismissed,
    updated_at = CURRENT_TIMESTAMP;
```

### `get_stats_context(repo)`

Queries `review_stats` for the repo, filters to rows where `total >= 5` (minimum
sample size), and produces guidance text:

- Rows with `dismissed / total > 0.8` and file_pattern ≠ `*`:
  `- {pass} pass: 100% dismissed ({dismissed}/{total}) in {file_pattern} files → suppress {severity} findings here`
- Rows with `dismissed / total > 0.5`:
  `- {pass} pass: {pct}% dismissed ({dismissed}/{total}) for '{severity}' severity → prefer higher severity`
- Rows with `accepted / total > 0.8`:
  `- {pass} pass: {pct}% accepted ({accepted}/{total}) → continue current approach`
- Otherwise: no guidance line generated

Returns `None` if no rows meet the sample-size threshold (first reviews, new
repo).

### Edge cases

- No feedback rows exist for a finding → counted in `total` but not in
  `accepted`/`dismissed`. Stats wait until feedback arrives.
- All findings dismissed → generates suppression hints.
- All findings accepted → generates "continue" hints.
- Multiple file patterns for same pass/severity → separate guidance lines.
- Stats refresh runs *after* findings are persisted, before the *next* review
  uses them. First review of a PR uses stats from prior PRs.

## Module 2 — `audit/reflector.py`

### `PatternReflector`

```python
REFLECTION_THRESHOLD = 5    # min unprocessed feedback items
MAX_RULES = 5               # max rules stored and injected

class PatternReflector:
    def __init__(self, agent: Agent, store: MemoryStore) -> None: ...

    async def maybe_reflect(
        self, repo: str, cwd: str | Path | None = None
    ) -> list[dict]:
        """If unprocessed feedback >= threshold, run a reflection pass.
        Returns newly learned rules (may be empty). Never raises."""
        ...
```

### `maybe_reflect(repo, cwd)`

1. Query `reflection_state` for `last_feedback_id`. If no row exists, create one
   with `last_feedback_id = 0`.
2. Query new feedback + findings:
   ```sql
   SELECT fb.id AS fb_id, fb.action, fb.finding_id,
          f.pass, f.severity, f.file, f.title, f.description
   FROM feedback fb
   JOIN findings f ON f.id = fb.finding_id
   WHERE f.repo = ? AND fb.id > ?
   ORDER BY fb.id
   ```
3. If count < `REFLECTION_THRESHOLD` → return `[]`.
4. Build a **reflection prompt** (see next section).
5. Run via `subprocess.run` using the agent's `build_command()` + prompt input.
   Single call, not a review pass. Timeout: 120 s.
6. Parse JSON output → validate against expected schema.
7. Insert each rule into `learned_rules` with `evidence_count = <number of
   dismissals cited>`, `confidence = <from JSON>`.
8. Update `reflection_state.last_feedback_id` to max feedback id processed.
9. Return list of inserted rule dicts.

### Reflection prompt template

```
You are analyzing past code review outcomes to improve future reviews.

Below are findings that were accepted (helpful) or dismissed across multiple
review passes for this repository.

ACCEPTED:
- [{pass}] "{title}" (file: {file}, severity: {severity}) — accepted
  ...

DISMISSED:
- [{pass}] "{title}" (file: {file}, severity: {severity}) — dismissed
  ...

Analyze these patterns. Output rules ONLY about patterns that were dismissed
2+ times across the same pass or file pattern. Each rule must be a general
principle the team follows — NOT a specific finding. Rules must be 1 sentence,
imperative tone, and actionable (an AI reviewer should be able to apply it).

Return ONLY a JSON array. No explanation text before or after.

[
  {
    "rule": "Do not flag naming conventions in API-facing functions; the team uses camelCase for public APIs",
    "evidence": "2 dismissals: snake_case in api.py, naming in api_helpers.py",
    "confidence": 0.9
  }
]

If no clear patterns emerge, return: []
```

The prompt includes ALL unprocessed feedback (accepted + dismissed). The LLM
needs both to contrast what was rejected vs. what was valued.

The prompt omits individual suggestion text from findings (too long for a batch
of 5+ items). It includes title + file + pass + severity + outcome.

### Failure modes

| Fault | Behavior |
|---|---|
| `subprocess.run` raises `FileNotFoundError` | `logger.warning`, return `[]`. Agent binary missing — can't reflect. |
| `subprocess.TimeoutExpired` (120 s) | `logger.warning`, return `[]`. Reflection prompt may be large. |
| Agent returns non-zero exit | `logger.warning` with stderr, return `[]`. |  
| JSON parse failure | `logger.warning` with raw output excerpt, return `[]`. |
| Individual rule item fails validation | Skip that item, log warning, continue with valid rules. |

Reflection failure never blocks the review. The next review attempt will
retry the same unprocessed feedback (last_feedback_id unchanged).

## Module 3 — `audit/guidelines.py`

```python
def assemble_learned_context(
    stats_text: str | None,
    rules: list[dict],
    max_rules: int = MAX_RULES,
) -> str | None:
```

**Logic:**
1. Sort rules by `confidence` descending, then by `created_at` descending.
2. Take top `max_rules`.
3. Build output string:
   ```
   Based on past review outcomes, the team has implicit preferences:

   **Statistical guidance:**
   {stats_text}

   **Inferred rules:**
   1. {rule_1} (confidence: {conf_1:.0%}, {evidence_1})
   2. ...
   ```
4. If `stats_text` is None and no rules → return `None`.
5. If `stats_text` is None but rules exist → omit the "Statistical guidance"
   subsection.
6. If `stats_text` exists but no rules → omit the "Inferred rules" subsection.

## Prompt changes — `review/prompts.py`

New kwarg `learned_context: str | None = None` on `build_prompt()`.

New prompt section inserted between **Relevant Design Specs & Plans** and
**PR Description**:

```
### Learned Review Guidelines
{learned_context or "No learned guidelines yet. Guidelines form as feedback accumulates over multiple reviews."}
```

Rationale for ordering:
1. **Project Conventions** — authoritative, from repo docs
2. **Relevant Design Specs & Plans** — authoritative, spec-compliant intent
3. **Learned Review Guidelines** — *inferred* intent from feedback; should inform but not override conventions/specs
4. **PR Description** — claim of what this PR does
5. **Changed Files (diff)** — the code itself
6. Static signals, usages, file context — deterministic grounding
7. **Past Feedback** — historical reference, not active guidance

The section always renders (with placeholder when empty) so the agent knows the
mechanism exists and can expect it in future reviews.

## Config — `config.py`

```python
class Config(BaseModel):
    ...
    learned_review: bool = True
    reflection_threshold: int = 5
    max_learned_rules: int = 5
```

**`learned_review: false`** disables the entire pipeline: no stats refresh, no
stats query, no reflection trigger, no learned context injection. The prompt
section still renders with the placeholder (consistent UX).

## CLI wiring — `cli.py`

In `_run_review`, after persisting findings and watermark (if progressive):

```python
learned_context: str | None = None
if config.learned_review and store is not None:
    aggregator = StatsAggregator(store)
    await aggregator._refresh(repo)
    stats_text = await aggregator.get_stats_context(repo)

    reflector = PatternReflector(agent=engine.agent, store=store)
    new_rules = await reflector.maybe_reflect(repo, cwd=root)

    # Combine newly learned rules with all existing rules from memory
    all_rules = await store.get_learned_rules(repo, limit=config.max_learned_rules)

    learned_context = assemble_learned_context(
        stats_text, all_rules, config.max_learned_rules
    )
```

Then `learned_context` passes to `engine.review()` → `build_prompt()`.

**Threading:** The aggregator's `_refresh` must complete before
`get_stats_context`. Both are fast SQL operations (< 50 ms). The reflector's
`maybe_reflect` is a subprocess call that may take up to 120 s. All three run
sequentially after the main review completes, before the next review cycle. They
do not compete with the parallel review passes.

### `--no-memory` interaction

When `--no-memory` or `config.memory == false`: no store → no stats, no
reflection. `learned_review` silently degrades to placeholder. Same behavior as
progressive review's interaction with memory.

## Server-mode wiring — `server/worker.py`

In `_run_review_for_job`, after persisting findings + watermark (if progressive):

```python
learned_context = None
if config.learned_review and store is not None:
    aggregator = StatsAggregator(store)
    await aggregator._refresh(repo_key)
    stats_text = await aggregator.get_stats_context(repo_key)

    reflector = PatternReflector(agent=engine.agent, store=store)
    new_rules = await reflector.maybe_reflect(repo_key, cwd=job.clone_path)

    all_rules = await store.get_learned_rules(repo_key, limit=config.max_learned_rules)
    learned_context = assemble_learned_context(
        stats_text, all_rules, config.max_learned_rules
    )
```

The server already has a `MemoryStore` instance. The agent is constructed from
the review engine which is already available in the worker.

Stats and reflection run *after* the review completes and findings are
persisted. They do not add latency to the check-run result.

## Flow summary

```
Review cycle N completes
    │
    ├─ Persist findings (existing)
    ├─ Persist watermark (existing, if progressive)
    │
    ├─ [NEW] StatsAggregator._refresh(repo)
    │         └─ Upsert review_stats from findings + feedback
    │
    └─ [NEW] PatternReflector.maybe_reflect(repo)
              └─ If unprocessed feedback ≥ threshold:
                    1. Build reflection prompt
                    2. Invoke agent CLI (subprocess, 120 s timeout)
                    3. Parse JSON rules → insert into learned_rules
                    4. Update reflection_state.last_feedback_id

Review cycle N+1 begins
    │
    ├─ StatsAggregator.get_stats_context(repo) → stats_text or None
    ├─ MemoryStore.get_learned_rules(repo, limit) → all existing rules
    ├─ assemble_learned_context(stats_text, all_rules) → learned_context
    │
    └─ engine.review(..., learned_context=learned_context)
           └─ build_prompt(..., learned_context=learned_context)
              └─ ### Learned Review Guidelines
```

Stats from cycle N are available for cycle N+1. Reflection from cycle N produces
rules available for cycle N+1. The gap is intentional — the review that produced
the findings should not be influenced by stats derived from those findings (no
self-referential loops).

## Edge cases

| Case | Behavior |
|---|---|
| First review ever on a repo | No stats rows, no rules → placeholder "No learned guidelines yet." |
| Feedback exists but < 5 new items | Stats rendered, reflection skipped, existing rules from prior reflections still injected |
| All findings accepted (no dismissals) | Stats show "100% accepted → continue"; reflection produces `[]` |
| All findings dismissed | Stats show "100% dismissed → suppress"; reflection may infer strong rules |
| Reflection LLM call fails | Warning logged, `[]` returned, review continues. Retries next cycle when more feedback arrives |
| Stats table has rows but all < 5 sample size | `get_stats_context` returns `None` → guidance subsection omitted |
| `learned_review: false` in config | Entire pipeline skipped. Prompt section still renders with placeholder |
| `--no-memory` or `config.memory: false` | No store → pipeline skipped. Same as progressive interaction |
| Learned rule becomes stale (never applied for >30 days) | Not yet implemented; `last_applied_at` column preserved for future use |
| Multiple repos (CLI reviews different repos) | Stats and rules are per-repo; no cross-contamination |

## Testing

All `subprocess.run` / `aiosqlite` calls mocked per existing conventions. No
real network or LLM calls.

### New: `tests/test_audit_stats.py`

- `_refresh` creates rows for all pass/severity/file_pattern combinations from
  findings+feedback.
- `_refresh` upserts correctly (run twice, second run updates counts).
- `get_stats_context` returns `None` when no rows meet `total >= 5`.
- `get_stats_context` formats high-dismissal rows correctly.
- `get_stats_context` formats high-acceptance rows correctly.
- `get_stats_context` omits rows with total < 5.
- File pattern classification: test file, migration file, config file, default.
- Empty repo (no findings) → `_refresh` succeeds silently, `get_stats_context`
  returns `None`.

### New: `tests/test_audit_reflector.py`

- `maybe_reflect` returns `[]` when unprocessed feedback < threshold.
- `maybe_reflect` builds correct prompt shape (accepted + dismissed sections,
  finding details).
- `maybe_reflect` invokes agent with correct argv and prompt input.
- Valid JSON response → rules inserted into `learned_rules`.
- Valid JSON response → `reflection_state.last_feedback_id` updated to max
  processed.
- Empty JSON `[]` response → no rules inserted, but `last_feedback_id` updated
  (feedback was processed, just no patterns found).
- `subprocess.CalledProcessError` → caught, warning logged, `[]` returned.
- `subprocess.TimeoutExpired` → caught, warning logged, `[]` returned.
- Malformed JSON output → warning logged with excerpt, `[]` returned.
- Missing `reflection_state` row → created with `last_feedback_id = 0`, then
  processed.
- Rules sorted by confidence descending in output list.

### New: `tests/test_audit_guidelines.py`

- Both inputs non-None → combined output with subsections.
- `stats_text` only → no "Inferred rules" subsection.
- Rules only → no "Statistical guidance" subsection.
- Both None → returns `None`.
- More rules than `max_rules` → capped, top by confidence.
- Empty rules list + stats None → returns `None`.

### Extended: `tests/test_prompts.py`

- `learned_context` non-None → new section present with content.
- `learned_context` None → placeholder text rendered.
- Section ordering verified: Specs → Learned Guidelines → PR Description.
- Existing sections unchanged when `learned_context` is None.

### Extended: `tests/test_integration.py`

- End-to-end with mocked agent: stats table populated, reflection triggered,
  learned context injected into prompt.
- `learned_review: false` → no stats/reflection DB calls, prompt has placeholder.
- `--no-memory` → no pipeline calls.
- Reflection failure → review still produces results.

## Files touched

- `src/superseded/audit/__init__.py` — **new**
- `src/superseded/audit/stats.py` — **new** — `StatsAggregator`
- `src/superseded/audit/reflector.py` — **new** — `PatternReflector`
- `src/superseded/audit/guidelines.py` — **new** — `assemble_learned_context`
- `src/superseded/memory/store.py` — new tables in `SCHEMA` and `_migrate`;
  `get_learned_rules`, `get_reflection_state`, `set_reflection_state` methods
- `src/superseded/config.py` — `learned_review`, `reflection_threshold`,
  `max_learned_rules` fields
- `src/superseded/review/prompts.py` — `learned_context` kwarg, new section
- `src/superseded/cli.py` — stats/reflection/assembly wiring in `_run_review`
- `src/superseded/server/worker.py` — same wiring in server worker
- `tests/test_audit_stats.py` — **new**
- `tests/test_audit_reflector.py` — **new**
- `tests/test_audit_guidelines.py` — **new**
- `tests/test_prompts.py` — extended
- `tests/test_integration.py` — extended

## Risks

- **LLM reflection cost.** Each reflection pass is one subprocess call to the
  configured AI CLI (cost varies by agent/model). Mitigated by threshold gating
  — reflection only runs every ~5 feedback events, which for most repos means
  every few PRs. Users can disable with `learned_review: false`.
- **Reflection prompt size.** Growth is unbounded if feedback accumulates
  without triggering (always just below threshold). Mitigated: we process ALL
  unprocessed feedback on each trigger, so the queue drains fully. Worst case is
  a single large reflection on first trigger (many PRs without feedback, then
  suddenly 20+). 120 s timeout + agent context window provide natural bounds.
- **Stale or wrong learned rules.** The agent may infer incorrect patterns.
  Mitigated by confidence tracking + the fact that rules are prompt-only
  guidance (not enforced programmatically). Future: users can react to learned
  rules with feedback, decrementing confidence until the rule is suppressed.
- **Per-repo isolation.** Same repo reviewed from CLI and server produces
  independent stats/rules (different DB files). Intentional — no cross-sync
  between entry points. Matches progressive review's design.
