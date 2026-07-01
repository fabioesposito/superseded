# Review Command

## Input Modes

Pick exactly one per invocation:

```bash
# PR mode — fetch diff via GitHub API
superseded review --pr 123

# Local diff mode — any git range
superseded review --diff main..feature
superseded review --diff HEAD~3..HEAD

# File mode — diff specific files against HEAD
superseded review src/auth.py src/models.py
```

You can combine `--diff` with file arguments to scope the diff:

```bash
superseded review --diff main..feature src/auth.py
```

## Output Formats

```bash
superseded review --pr 123 --format table      # default — compact table with color
superseded review --pr 123 --format json       # machine-readable
superseded review --pr 123 --format markdown   # for docs/PR bodies
```

**Table** (default): Columns for severity (colored icon), pass name, file, line, title. Summary footer with per-severity counts.

**JSON**: Array of finding objects, each with `id`, `pass_name`, `severity`, `file`, `line`, `end_line`, `title`, `description`, `suggestion`, `confidence`, `reasoning`.

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

Superseded injects context into every AI prompt so the agent sees more than just the diff:

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

## Agent Selection

```bash
# Explicit agent
superseded review --pr 123 --agent claude-code

# Explicit model
superseded review --pr 123 --agent codex --model gpt-5.4-mini

# Environment variables override everything
export SUPERSEDED_AGENT=opencode
export SUPERSEDED_MODEL=deepseek-v4-pro
superseded review --pr 123
```

Precedence: **env vars > CLI flags > config file**.

## Timeout

Per-pass timeout defaults to 600 seconds. Increase for very large diffs:

```bash
superseded review --pr 123 --timeout 900
```

If a single pass times out or fails, it logs a warning and the review continues with other passes.

## Memory Disable

```bash
superseded review --pr 123 --no-memory
```

Disabling memory prevents loading past dismissed findings and learned rules. The memory database at `.superseded/memory.db` is still written to, but prior data is not injected into the prompt.
