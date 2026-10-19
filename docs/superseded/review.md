# Review Command

## Input Modes

```bash
# Auto-detect mode (no args) — diff the working tree against HEAD
superseded review

# Staged-only mode — review the index (git diff --cached)
superseded review --staged

# PR mode — fetch diff via GitHub API
superseded review --pr 123

# Local diff mode — any git range
superseded review --diff main..feature
superseded review --diff HEAD~3..HEAD

# File mode — diff specific files against HEAD
superseded review src/auth.py src/models.py
```

With no arguments, `review` runs `git diff HEAD` (everything uncommitted: staged + unstaged). `--staged` scopes it to staged changes only (`git diff --cached`). If there are no changes to review, the command exits cleanly with a message instead of running an empty review.

You can combine `--diff` with file arguments to scope the diff:

```bash
superseded review --diff main..feature src/auth.py
```

`--staged` only takes effect on the no-args branch; combining it with `--diff` or file arguments is allowed but has no effect (the explicit diff wins).

## Output Formats

```bash
superseded review --pr 123 --format table      # default — compact table with color
superseded review --pr 123 --format json       # machine-readable
superseded review --pr 123 --format markdown   # for docs/PR bodies
```

**Table** (default): Columns for severity (colored icon), pass name, file, line, title. Summary footer with per-severity counts.

**JSON**: An object with `findings`, `warnings`, and convergence metadata:

```json
{
  "findings": [],
  "warnings": [],
  "carried_findings": [],
  "round_number": 0,
  "at_round_cap": false,
  "suppressed_count": 0,
  "convergence_note": ""
}
```

Each finding has `id`, `pass_name`, `severity`, `file`, `line`, `end_line`, `title`, `description`, `suggestion`, `confidence`, `reasoning`, plus the grounding fields `evidence`, `evidence_verified`, `pre_existing`, and `duplicate_of_static`. Verifier fields (`verification`, `verified_severity`, `verification_reason`) are populated when the verification pass runs. The `warnings` array lists any passes that failed and were skipped (empty when all passes succeeded), so a `findings: []` result is never confused with a clean run — pipe with `jq '.findings[]'` and check `.warnings` for pass failures. `carried_findings` lists unresolved critical/important findings from earlier rounds; these are not re-posted as new comments.

**Markdown**: Grouped by severity with collapsible reasoning details. Suitable for pasting into issue bodies.

## The Five Review Passes

| Pass | Focus | Example findings |
|---|---|---|
| `security` | Injection, auth bypass, secret exposure, unsafe deserialization, path traversal, SSRF, XSS | "SQL query built with string interpolation", "Hardcoded API key" |
| `correctness` | Logic errors, off-by-one, null handling, race conditions, error handling gaps | "Missing None check before attribute access", "Unhandled exception path" |
| `performance` | N+1 queries, unnecessary allocations, blocking I/O in async paths, O(n²) patterns | "Query inside loop — N+1", "sync I/O in async handler" |
| `style` | Unclear naming, dead code, complex logic, inconsistent patterns, missing type hints | "Unused import", "Function exceeds complexity threshold" |
| `architecture` | Coupling, API contract changes, dependency direction, public interface changes | "Circular import introduced", "Breaking change to public API" |

### Selecting Passes

```bash
# Run only security and correctness
superseded review --pr 123 --passes security,correctness

# Disable specific passes in config:
# .superseded.yaml
passes:
  style: false
  architecture: false
```

## Posting to GitHub

```bash
# Review PR and post inline comments
superseded review --pr 123 --post
```

Each finding becomes an inline PR review comment on the relevant line. Findings whose line numbers fall outside the actual diff hunks are listed in the review summary body instead.

Comments carry severity plus `PRE-EXISTING`, `UNVERIFIED`, and `STATIC` tags when applicable, and a hidden fingerprint marker used for repeat suppression. Pre-existing findings never set `REQUEST_CHANGES`. The review event is `REQUEST_CHANGES` when a new non-pre-existing critical/important finding exists **or** a carried blocking finding is still open; otherwise it is `COMMENT`. Carried findings are listed by title and `file:line` so a blocked PR always says what is still open.

## Context Grounding

Superseded injects context into every AI prompt so the model sees more than just the diff:

| Context source | What it provides | Controlled by |
|---|---|---|
| **File context** | ±20 lines around each changed hunk | Always on |
| **Conventions** | `REVIEW.md` (first), `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `CONTRIBUTING.md`, `.editorconfig` | `conventions: false` / `--no-conventions` |
| **Specs & plans** | Relevant `docs/superseded/specs/*.md` and `plans/*.md` matching changed files | `spec_retrieval: false` / `--no-specs` |
| **Static analysis** | ruff, mypy, bandit, eslint, tsc, gofmt, go vet, staticcheck, gitleaks; advisory findings duplicating these signals are dropped | `static_analysis: false` / `--no-static` |
| **Usage retrieval** | Cross-file callers of changed symbols (via `rg`) | `usage_retrieval: false` / `--no-usage` |
| **Graph retrieval** | Alternative to usage retrieval — callers via `code-review-graph` | `graph: false` / `--no-graph` |

Disable any source to speed up reviews or reduce token usage:

```bash
superseded review --pr 123 --no-static --no-conventions
```

## Progressive Review

When reviewing PRs with memory enabled (default), superseded tracks which commits it has already reviewed. Subsequent runs only review the new commits:

```bash
# First review — reviews entire PR
superseded review --pr 123

# Push more commits — reviews only the new ones
superseded review --pr 123

# Force full review despite watermark
superseded review --pr 123 --full
```

Progressive review needs `memory: true` and a PR number. It is **skipped** when memory is disabled (falls back to full review) or when the head diverges (force-push/rebuild triggers a full review).

### Convergence Across Rounds

Diff-only review alone can make an unresolved issue disappear when the author pushes an unrelated commit. Superseded therefore stores PR-scoped finding state in the `pr_findings` table (keyed by repo, PR number, and a line-agnostic fingerprint) and a `review_round` counter:

- **Carry forward:** unresolved critical/important findings stay visible and keep the check/PR blocked even when the new diff is clean.
- **Suppress repeats:** a new finding matching an already-open or dismissed fingerprint is not posted again.
- **Resolve, then re-raise if needed:** a resolved thread marks the finding `resolved`, not `dismissed`; if the same issue genuinely reappears, it can be posted again.
- **Cap the loop:** `max_review_rounds` (default 3) caps repeated rounds. Past the cap only critical findings are posted, with a note to step back and reconsider the approach.

Convergence runs for posted PR reviews (`--post` and server `post: true`). A `--no-post` dry run is single-shot: it does not read or mutate `pr_findings` and does not advance the round.

```yaml
# .superseded.yaml
max_review_rounds: 3
```

## Provider Selection

```bash
# Explicit provider
superseded review --pr 123 --provider deepseek
superseded review --pr 123 --provider openai
superseded review --pr 123 --provider anthropic
superseded review --pr 123 --provider qwen

# Explicit model
superseded review --pr 123 --provider deepseek --model deepseek-v4-flash

# Environment variables override everything
export SUPERSEDED_PROVIDER=deepseek
export SUPERSEDED_MODEL=deepseek-v4-flash
superseded review --pr 123
```

`--provider` accepts `deepseek` (default), `openai`, `anthropic`, or `qwen`.
Each provider needs its own API key — set `SUPERSEDED_DEEPSEEK_API_KEY`
(platform.deepseek.com), `SUPERSEDED_OPENAI_API_KEY` (platform.openai.com),
`SUPERSEDED_ANTHROPIC_API_KEY` (console.anthropic.com), or
`SUPERSEDED_QWEN_API_KEY` (Alibaba Cloud Model Studio / DashScope) for the
provider you select. `SUPERSEDED_AGENT` still works as a deprecated alias for
`SUPERSEDED_PROVIDER`.

Default models:

| Provider | Default model |
|---|---|
| `deepseek` | `deepseek-v4-flash` |
| `openai` | `gpt-5.6-terra` |
| `anthropic` | `claude-sonnet-5` |
| `qwen` | `qwen3.8-max` |

Precedence: **env vars > CLI flags > config file**.

## Logging

By default the CLI is quiet at the `WARNING` level with human-formatted log lines on stderr (progress messages via `--format` output are untouched on stdout). For structured/machine-readable logs — useful when piping stderr to a log shipper — switch to JSON:

```bash
# Structured JSON logs on stderr
superseded --log-format json review --diff HEAD~3..HEAD

# Raise verbosity
superseded --log-level INFO review --pr 123
```

`--log-format` accepts `text` (default) or `json`; `--log-level` accepts any standard level name (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`). Server mode defaults to JSON logging. Precedence is **env vars > CLI flags > config file**, same as provider/model:

```bash
export SUPERSEDED_LOG_FORMAT=json
export SUPERSEDED_LOG_LEVEL=INFO
superseded review
```

## Timeout

Per-pass timeout defaults to 600 seconds. Increase for very large diffs:

```bash
superseded review --pr 123 --timeout 900
```

If a single pass times out or fails, it logs a warning and the review continues with the other passes. A pass whose findings don't match the output schema (e.g. an invalid `severity`) is retried once with a corrective prompt before being skipped.

## Grounding and Verification

After merge, a deterministic grounding layer runs in this order:

1. **Static-duplicate filter** — advisory (`suggestion`/`nit`) findings that duplicate static-analysis output are dropped; blocking duplicates are kept and tagged `STATIC`.
2. **Pre-existing marking** — findings outside the changed hunks are tagged `PRE-EXISTING` and never block the PR.
3. **Verification pass** (optional) — the extra provider call described below.
4. **Evidence validation** — every finding should quote 1–3 lines of changed code in `evidence`. Unverified advisory findings are dropped; unverified critical/important findings are kept but tagged `UNVERIFIED`.
5. **Severity re-rank** — findings are ordered again after verification.

Verification is on by default; disable it to save tokens:

```bash
superseded review --pr 123 --no-verify
# or: verify: false in .superseded.yaml, or SUPERSEDED_VERIFY=0
```

## Offline Eval

Run the reviewer against the case corpus without touching a PR:

```bash
superseded eval tests/evals/cases.yaml --dry-run   # validate the corpus
superseded eval tests/evals/cases.yaml             # run it (uses the configured provider)
superseded eval tests/evals/cases.yaml --format json
```

Each case contains a diff, optional file context, expected findings, and forbidden findings. Matching is by file, line within ±2, case-insensitive title substring, and optional exact severity. The command exits 1 if any case misses an expected finding or hits a forbidden one. See [Offline Eval](eval.md).

## Exit Codes

The `review` command exits with:

- `0` — review completed cleanly (with or without findings).
- `1` — hard error (provider unavailable, diff fetch failed, etc.).
- `2` — usage / configuration error.
- `3` — **partial review**: the run completed and emitted output, but at least one pass was skipped (e.g. a transient provider failure). Output is still printed and persisted before this exit, so CI can detect infra degradation without losing the partial results — pair it with `jq '.warnings'` on the JSON output for the list of skipped passes.

## Memory Disable

```bash
superseded review --pr 123 --no-memory
```

Disabling memory prevents loading past dismissed findings, learned rules, progressive watermarks, and convergence state. The memory database at `.superseded/memory.db` is still written to, but prior data is not injected into the prompt and PR rounds do not advance.
