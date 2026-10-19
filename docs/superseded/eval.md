# Offline Eval

Superseded ships a small, provider-agnostic eval harness for reviewer changes. Use it when you edit prompts, grounding rules, convergence behavior, or model settings and want evidence that quality did not regress.

## Running It

```bash
# Validate the corpus without a provider or API key
superseded eval tests/evals/cases.yaml --dry-run

# Run the corpus with the configured provider/model
superseded eval tests/evals/cases.yaml

# Machine-readable report
superseded eval tests/evals/cases.yaml --format json

# Run every YAML file in a directory
superseded eval tests/evals
```

`--provider` and `--model` work like they do on `review`. The command exits `0` when every case passes, `1` when any case fails, and `2` on a usage/config/load error.

## What a Case Contains

`tests/evals/cases.yaml` is a top-level YAML list. Each case has:

| Field | Required | Meaning |
|---|---|---|
| `name` | Yes | Unique case name |
| `diff` | Yes | Unified diff passed to the review engine |
| `files` | No | Map of file path to file contents, injected as file context |
| `passes` | No | Pass subset; omit to use the configured passes |
| `expected` | Yes | Findings the reviewer must produce (may be empty) |
| `forbidden` | No | Findings that would make the case fail |

An expected/forbidden entry has:

```yaml
- file: app.py
  line: 3
  title_contains: SQL injection   # case-insensitive substring
  severity: critical              # optional exact match
```

Expected and forbidden line numbers must fall inside the diff's new-side hunk windows. The corpus test in `tests/test_eval.py` checks this for every case.

## Scoring

A finding matches an expected entry when:

- the file is the same
- the line is within ±2 of the expected line
- `title_contains` is a case-insensitive substring of the finding title
- `severity` matches exactly, when specified

Each finding and each expected entry can match at most once. A case passes when recall is `1.0` and no forbidden finding matched. Precision is reported but does not by itself fail a case.

The report shows per-case `PASS`/`FAIL`, recall, precision, and aggregate macro averages. A missing expected finding or a forbidden hit is a regression.

## Current Corpus

`tests/evals/cases.yaml` has 20 cases covering:

- security: SQL injection, command injection, hardcoded secret, unsafe deserialization, path traversal
- correctness: off-by-one, null dereference, swallowed exception, wrong comparison, race condition
- performance: N+1 query, quadratic loop, blocking I/O in async code, unnecessary copy, missing index
- style/architecture: dead code, circular dependency, API contract break
- clean diffs with forbidden false-positive traps

The corpus is intentionally small. Grow it from real misses, dismissed findings, and regressions rather than adding generic examples.
