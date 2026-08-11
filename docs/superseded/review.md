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

**JSON**: An object `{"findings": [...], "warnings": [...]}`. Each finding has `id`, `pass_name`, `severity`, `file`, `line`, `end_line`, `title`, `description`, `suggestion`, `confidence`, `reasoning`. The `warnings` array lists any passes that failed and were skipped (empty when all passes succeeded), so a `findings: []` result is never confused with a clean run — pipe with `jq '.findings[]'` and check `.warnings` for pass failures.

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

Each finding becomes an inline PR review comment on the relevant line. Findings whose line numbers fall outside the actual diff hunks are listed in the review summary body instead. The review event is `REQUEST_CHANGES` when critical/important findings exist, otherwise `COMMENT`.

## Context Grounding

Superseded injects context into every AI prompt so the model sees more than just the diff:

| Context source | What it provides | Controlled by |
|---|---|---|
| **File context** | ±20 lines around each changed hunk | Always on |
| **Conventions** | `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `CONTRIBUTING.md`, `.editorconfig` | `conventions: false` / `--no-conventions` |
| **Specs & plans** | Relevant `docs/superseded/specs/*.md` and `plans/*.md` matching changed files | `spec_retrieval: false` / `--no-specs` |
| **Static analysis** | ruff, mypy, bandit, eslint, tsc, go vet, staticcheck, gitleaks | `static_analysis: false` / `--no-static` |
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

## Provider Selection

```bash
# Explicit provider
superseded review --pr 123 --provider deepseek
superseded review --pr 123 --provider openai
superseded review --pr 123 --provider anthropic

# Explicit model
superseded review --pr 123 --provider deepseek --model deepseek-v4-flash

# Environment variables override everything
export SUPERSEDED_PROVIDER=deepseek
export SUPERSEDED_MODEL=deepseek-v4-flash
superseded review --pr 123
```

`--provider` accepts `deepseek` (default), `openai`, or `anthropic`. Each
provider needs its own API key — set `SUPERSEDED_DEEPSEEK_API_KEY`
(platform.deepseek.com), `SUPERSEDED_OPENAI_API_KEY` (platform.openai.com), or
`SUPERSEDED_ANTHROPIC_API_KEY` (console.anthropic.com) for the provider you
select. `SUPERSEDED_AGENT` still works as a deprecated alias for
`SUPERSEDED_PROVIDER`.

Default models:

| Provider | Default model |
|---|---|
| `deepseek` | `deepseek-v4-flash` |
| `openai` | `gpt-5.6-terra` |
| `anthropic` | `claude-sonnet-5` |

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

Disabling memory prevents loading past dismissed findings and learned rules. The memory database at `.superseded/memory.db` is still written to, but prior data is not injected into the prompt.
