# Post-Review AI Verification Stage — Design

**Date:** 2026-08-03
**Status:** Draft

## Problem

Superseded runs 5 parallel passes (security, correctness, performance, style,
architecture), merges and deduplicates findings by `file+line+title`, and emits
the result. There is no post-processing step to validate the merged findings
against the original diff. False positives from any pass slip through
uncontested, and cross-pass contradictions or severity miscalibrations go
unresolved. Sashiko devotes an explicit Stage 10 ("Verification and severity
estimation") to this problem, and measuring the result is the only way to know
whether a prompt change helped or hurt.

## Goals / Non-goals

**Goals**

- An optional **sequential verification stage** that runs after all 5 parallel
  passes complete and findings are merged/deduplicated.
- The verifier re-examines each merged finding against the original diff and
  surrounding file context, returning a verdict for each: **keep** (possibly with
  re-estimated `severity`/`confidence`) or **drop** (false positive).
- Findings the verifier drops are recorded in the memory store with
  `source: "verifier"` so the learned-review subsystem treats them as
  AI-self-rejected — distinct from human dismissals.
- Verifier failure is non-fatal: if the verification pass itself errors or
  times out, the original merged findings are kept as-is and a warning is
  appended.
- CLI toggle `--verify`/`--no-verify` and config `verify: true|false`
  (default `true`, matching the existing `conventions: true` pattern).

**Non-goals**

- Changing the 5 primary pass prompts, the merger, or the existing structure
  of `ReviewEngine.review()`.
- Multi-round verification (one pass only).
- Token/cost tracking (deferred to a separate feature).
- A standalone `superseded verify` CLI subcommand (this is an engine-level
  post-processing step, not a separate user-facing command).

## Design

### Flow

```
5 parallel passes → merge_findings() → [if verify enabled] verify pass → final result
```

If verification is disabled or the merged findings list is empty, the verifier
is simply skipped.

### `Finding` model changes (`models.py`)

Two new optional fields:

```python
verification: Literal["kept", "dropped"] | None = None   # None = not verified
verified_severity: Severity | None = None                 # re-estimated severity
verification_reason: str | None = None                    # verifier's justification
```

When `verification` is `"kept"` and `verified_severity` is set, the finding's
`severity` is replaced with `verified_severity` at output time. When
`verification` is `"dropped"`, the finding is excluded from the final
`ReviewResult.findings` list.

These fields persist to the memory store so learned-review can distinguish
"verifier dropped it" from "human dismissed it" from "never verified".

### `Config` changes (`config.py`)

```python
verify: bool = True
```

Precedence: CLI `--verify` / `--no-verify` > env `SUPERSEDED_VERIFY` > config
file `verify: true|false`. The env-var path mirrors `graph`/`sandbox`
(`_env_truthy("SUPERSEDED_VERIFY")`).

`.superseded.yaml` gains `verify: false` when a user configures
`superseded init --no-verify` (or equivalent editing).

### Verification prompt (`review/prompts.py`)

```python
def build_verify_prompt(
    merged_findings: list[Finding],
    diff: str,
    file_context: str | None,
) -> str:
```

The prompt asks the agent to:

1. Re-read the diff and each finding
2. For each finding, determine if it's a **true positive** (`keep`) or a **false
   positive** (`drop`)
3. For kept findings, optionally re-estimate `severity` (using the same
   `critical|important|suggestion|nit` calibration) and `confidence`
4. For dropped findings, briefly state why (1 sentence)

The prompt includes:
- The full diff
- File context (±20 lines around changes)
- The merged findings as a JSON array
- The severity calibration anchors
- A rule saying "only drop a finding when the code clearly disproves it"
  (avoid Type II errors — dropping a real bug is worse than keeping noise)

Output format is a JSON array matching:

```json
[
  {
    "id": "correctness-a1b2c3d4e5f6",
    "action": "keep",
    "severity": "suggestion",
    "confidence": "low",
    "reason": "short justification"
  },
  {
    "id": "security-f7e8d9c0b1a2",
    "action": "drop",
    "reason": "The code already handles this case on line 42"
  }
]
```

### Verification executor (`review/engine.py`)

Added to `ReviewEngine`:

```python
def _run_verification(
    self,
    result: ReviewResult,
    diff: str,
    file_context: str | None,
    timeout: int,
    sess: Session,
) -> ReviewResult:
```

Steps:

1. Build `build_verify_prompt(result.findings, diff, file_context)`.
2. Run the agent with the verification prompt (using the same `sess` and
   `timeout` as the primary passes).
3. Parse the verifier output. The verification output uses a distinct schema
   (`{"id", "action", "severity", "confidence", "reason"}`) from standard findings,
   so a dedicated `_parse_verdicts()` helper is used rather than reusing
   `agent.parse_output()`. Map each verdict to its corresponding `Finding` by
   `id`. If a finding's `id` is not in the verifier's output, treat it as `kept`
   unchanged (the verifier may omit findings it has no opinion on). See
   `review/verifier.py` for the parser implementation.
4. For kept-but-re-estimated findings: update `severity` and `confidence`.
5. For dropped findings: set `verification = "dropped"`.
6. Construct a new `ReviewResult` with only `kept` findings.
7. Append a warning noting how many findings were dropped by the verifier
   (e.g. "Verification dropped 3 of 12 findings").
8. On any failure (timeout, unparseable output, non-zero exit), log a warning
   and return the original `result` unchanged.

Called from `ReviewEngine.review()` after `merge_findings()`:

```python
if self.config.verify and result.findings:
    result = self._run_verification(
        result, diff, file_context, timeout, sess
    )
```

### Memory store impact

When findings are persisted in `_post_review_store()`, the finding dicts passed
to `record_findings_batch()` include `verification` and `verification_reason`:

```python
{
    "id": f.id,
    "pass_name": f.pass_name,
    "severity": f.severity,
    "file": f.file,
    "line": f.line,
    "title": f.title,
    "description": f.description,
    "reasoning": f.reasoning,
    "verification": f.verification,          # new
    "verification_reason": f.verification_reason,  # new
}
```

Findings with `verification = "dropped"` are stored as `feedback` records with
`action = "dismissed"` and `source = "verifier"` so the `StatsAggregator` and
`PatternReflector` can distinguish AI-self-rejected findings from human
dismissals. The `source` column is added to the `feedback` table via the
existing self-migration path in `store.init()`.

### CLI changes (`cli.py`)

`review` command gains:

```python
@click.option(
    "--verify/--no-verify",
    "verify",
    default=None,
    help="Toggle post-merge verification pass (default: from config; env SUPERSEDED_VERIFY).",
)
```

And the env resolver:

```python
VERIFY_ENV = "SUPERSEDED_VERIFY"

def resolve_verify(cli_value: bool | None, config: Config) -> bool:
    env = os.environ.get(VERIFY_ENV)
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    if cli_value is not None:
        return cli_value
    return config.verify
```

### Output impact

No format changes needed — `format_table`, `format_json`, and `format_markdown`
already iterate over `result.findings`. Since dropped findings are excluded from
`result.findings`, the output is naturally cleaner. The verifier adds a
`result.warnings` entry like `"Verification completed: 3 findings dropped, 9
kept (2 re-estimated)"`.

### Server mode

The server worker (`server/worker.py`) passes `verify` from the incoming
request/payload through to the engine. The GitHub App check-run summary includes
the verification summary line in its conclusion text. No other server changes.

### Error handling

| Scenario | Behavior |
|---|---|
| Verifier agent exits non-zero | Warning logged, original findings kept |
| Verifier times out | Warning logged, original findings kept |
| Verifier output is unparseable JSON | Warning logged, original findings kept |
| Verifier output is valid JSON but missing some finding IDs | Missing IDs treated as `keep` unchanged |
| All findings dropped by verifier | Result is empty `ReviewResult`, no error |
| No findings to verify (empty merge) | Verifier skipped entirely |

### Performance

One additional agent invocation per review. With a 600s timeout, worst-case
adds 10 minutes to a review that already takes up to 5 × 600s = 50 minutes for
the parallel passes. In practice, verification prompts are much shorter than
primary pass prompts (they contain only findings, not the full diff-plus-context
payload), so the verifier typically completes in <60s.

## Testing

- `test_verification_keeps_valid.py` — verifier output with all `keep` actions
  preserves all findings
- `test_verification_drops_false_positives.py` — findings flagged as false
  positives are excluded from result
- `test_verification_reestimates_severity.py` — severity/confidence re-estimation
  is applied
- `test_verification_disabled.py` — when `verify=false`, verifier is never
  called and findings pass through unchanged
- `test_verification_empty_merge.py` — when merged findings is empty, verifier
  is skipped
- `test_verification_failure_graceful.py` — verifier timeout/error returns
  original result with warning
- `test_verification_missing_ids.py` — verifier output omitting some finding
  IDs treats them as `keep`
- `test_verify_config_precedence.py` — env > CLI > config resolution is correct
- `test_verify_prompt_structure.py` — the verification prompt contains expected
  sections (diff, file context, findings JSON, calibration)

All tests mock `sess.run()` to avoid real agent invocations, matching the
existing test pattern in `test_integration.py`.

## Migration / backward compatibility

- `verify` defaults to `true` in `Config`, so existing installations
  automatically get verification on upgrade.
- `.superseded.yaml` files written by older versions lack `verify`; `load_config`
  fills in the default.
- The `Finding` fields `verification` and `verified_severity` are `| None` with
  default `None`, so existing serialization/deserialization is unaffected.
- Memory store schema update is additive (new column or metadata field); no data
  migration required.
