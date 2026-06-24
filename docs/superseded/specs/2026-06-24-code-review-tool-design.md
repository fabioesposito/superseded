# Superseded — Code Review Tool Design

## Overview

Superseded is a multi-pass AI code review tool. It reviews pull requests and diffs by delegating to AI CLI tools (claude-code, opencode, codex) with focused prompts per review category. Results are output locally (JSON, markdown, table) and optionally posted as GitHub PR review comments.

The tool learns from human feedback — it tracks which review comments were dismissed or accepted and adjusts future reviews accordingly.

## Core Workflow

```
superseded review --pr 123
        │
        ▼
   Diff Fetcher (gh pr diff / git diff)
        │
        ▼
   Feedback Check (reactions/resolutions on past comments)
        │
        ▼
   Memory Store Update
        │
        ▼
   Review Engine (parallel passes)
   ┌──────────────────────────────┐
   │  Security    ──► AI CLI      │
   │  Correctness ──► AI CLI      │
   │  Performance ──► AI CLI      │
   │  Style       ──► AI CLI      │
   │  Architecture──► AI CLI      │
   └──────────────────────────────┘
        │
        ▼
   Result Merger (dedupe, rank by severity)
        │
        ▼
   Output (JSON / markdown / table / GitHub PR comments)
```

## Interfaces

### CLI

```bash
# Review a PR
superseded review --pr 123

# Review a local diff
superseded review --diff HEAD~3..HEAD

# Review specific files
superseded review src/auth.py src/api.py

# Choose agent + model
superseded review --pr 123 --agent claude-code --model claude-sonnet-4-20250514

# Output format
superseded review --pr 123 --format json
superseded review --pr 123 --format markdown
superseded review --pr 123 --format table  # default

# Post to GitHub PR
superseded review --pr 123 --post

# Check/update feedback on past reviews
superseded feedback --check

# Manually mark feedback
superseded feedback <comment-id> --helpful
superseded feedback <comment-id> --dismiss
```

### GitHub Action

```yaml
# .github/workflows/review.yml
name: Code Review
on:
  pull_request:
    types: [opened, synchronize]

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: superseded/review@v1
        with:
          agent: claude-code
          model: claude-sonnet-4-20250514
          passes: security,correctness,performance
          post: true
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

The action is a Docker container running `superseded review --pr $PR_NUMBER --post`.

## Review Passes

Each pass is an independent AI CLI call with a focused prompt. Passes run in parallel.

| Pass | Focus | Prompt emphasis |
|------|-------|----------------|
| **Security** | Vulnerabilities, injection, auth issues, secrets | OWASP top 10, common vuln patterns |
| **Correctness** | Logic bugs, edge cases, off-by-one, null handling | Does the code do what the PR description claims? |
| **Performance** | N+1 queries, unnecessary allocations, blocking I/O | Hot paths, algorithmic complexity |
| **Style** | Naming, structure, dead code, complexity | Consistency with repo patterns, readability |
| **Architecture** | Separation of concerns, API design, coupling | Does this change fit the codebase's architecture? |

Users enable/disable passes via config:

```yaml
passes:
  security: true
  correctness: true
  performance: true
  style: false
  architecture: true
```

## Prompt Template

Each pass uses this template structure:

```
You are performing a {pass_name} code review.

## Your Role
{pass_specific_instructions}

## Rules
- Only report genuine issues, not style preferences unless they impact readability
- Be specific: cite exact file, line numbers, and code
- Provide actionable suggestions with code examples
- Do NOT report issues in unchanged code unless directly related to the change
- If there are no issues in this category, return an empty array

## Context

### PR Description
{pr_description or "No description provided"}

### Changed Files (diff)
{diff}

### File Context (surrounding code for changed files, ±20 lines from changes)
{file_context}

### Past Feedback (findings dismissed by humans — avoid similar)
{memory_context or "No past feedback"}

## Output Format
Return ONLY a JSON array. No explanation text before or after.

[
  {
    "severity": "critical|important|suggestion|nit",
    "file": "path/to/file.py",
    "line": 42,
    "end_line": 45,
    "title": "Short description",
    "description": "Detailed explanation of the issue",
    "suggestion": "Code fix or suggestion"
  }
]

If no issues found, return: []
```

### Pass-Specific Instructions

- **Security:** "Focus on: injection vulnerabilities, auth bypass, secret exposure, unsafe deserialization, path traversal, SSRF, XSS. Think like an attacker."
- **Correctness:** "Focus on: logic errors, off-by-one, null/undefined handling, race conditions, error handling gaps, incorrect assumptions. Does the code match the PR description?"
- **Performance:** "Focus on: N+1 queries, unnecessary allocations, blocking I/O in async paths, O(n²) where O(n) is possible, missing caching opportunities."
- **Style:** "Focus on: unclear naming, dead code, overly complex logic, inconsistent patterns with the rest of the codebase, missing type hints."
- **Architecture:** "Focus on: separation of concerns, API contract changes, dependency direction, coupling between modules, public interface changes."

## AI CLI Integration

The tool delegates to existing AI CLIs rather than calling LLM APIs directly.

**Supported agents:**
- `claude-code` — `claude --model {model} --print "{prompt}"`
- `opencode` — `opencode run "{prompt}"`
- `codex` — `codex "{prompt}"`

**Invocation:** Subprocess with prompt passed as argument. The tool checks that the CLI binary exists on `$PATH` before invoking it and fails with a clear error if not found.

**Output parsing:** Extract JSON array from AI response using regex. Fallback to markdown parsing if JSON extraction fails.

**Agent/model selection:**
- CLI: `--agent claude-code --model claude-sonnet-4-20250514`
- Config: `agent: claude-code` + `model: claude-sonnet-4-20250514`
- Env: `SUPERSEDED_AGENT` + `SUPERSEDED_MODEL`

## Output Format

### Finding Structure

```json
{
  "id": "sec-abc123",
  "pass": "security",
  "severity": "critical",
  "file": "src/auth.py",
  "line": 42,
  "end_line": 45,
  "title": "SQL injection in user query",
  "description": "User input is interpolated directly into SQL string...",
  "suggestion": "Use parameterized queries: cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))",
  "confidence": "high"
}
```

**Severity levels:** `critical`, `important`, `suggestion`, `nit`

### Local Output

- `--format json` — raw JSON array of findings
- `--format markdown` — human-readable grouped by severity
- `--format table` — terminal table (default)

### GitHub PR Output

- Posts findings as a single PR review with inline comments on relevant lines
- Labels the review with pass tags (e.g., "Security Review", "Performance Review")
- Critical/important findings: `REQUEST_CHANGES`
- Suggestions/nits: `COMMENT`

## Memory & Feedback

The tool learns from human responses to its review comments.

### Storage

SQLite database at `.superseded/memory.db`. Schema:

```sql
CREATE TABLE findings (
    id TEXT PRIMARY KEY,
    repo TEXT,
    pass TEXT,
    severity TEXT,
    file TEXT,
    line INTEGER,
    title TEXT,
    description TEXT,
    dismissed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id TEXT REFERENCES findings(id),
    action TEXT,  -- 'helpful', 'dismiss', 'resolved'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Feedback Collection

**At review start:** The tool checks for feedback on past review comments:
- Fetches reactions (👍/👎) on past review comments via `gh api`
- Checks if past review comments have been resolved
- Updates the memory store

**Manual feedback:**
```bash
superseded feedback <comment-id> --helpful
superseded feedback <comment-id> --dismiss
```

### Memory Injection

Past dismissed findings are injected into review prompts as a "lessons learned" section:

```
### Past Feedback (findings dismissed by humans — avoid similar)
- Style pass: "Missing type hints on line 42" — dismissed (project doesn't enforce type hints)
- Security pass: "Potential SQL injection" — helpful (was a real bug)
```

This prevents the tool from repeating findings humans have already rejected.

## Configuration

`.superseded.yaml` in repo root:

```yaml
agent: claude-code
model: claude-sonnet-4-20250514
passes:
  security: true
  correctness: true
  performance: true
  style: true
  architecture: true
post_to_pr: false
format: table
memory: true
```

CLI flags override config. Env vars override both.

## Project Structure

```
superseded/
├── pyproject.toml
├── src/
│   └── superseded/
│       ├── __init__.py
│       ├── cli.py              # CLI entry point (click/typer)
│       ├── config.py           # Config loader
│       ├── diff.py             # Diff fetching (gh / git)
│       ├── review/
│       │   ├── __init__.py
│       │   ├── engine.py       # Orchestrates passes
│       │   ├── passes/
│       │   │   ├── security.py
│       │   │   ├── correctness.py
│       │   │   ├── performance.py
│       │   │   ├── style.py
│       │   │   └── architecture.py
│       │   ├── prompts.py      # Prompt templates per pass
│       │   └── merger.py       # Dedupe + rank findings
│       ├── agents/
│       │   ├── __init__.py
│       │   ├── base.py         # Agent interface
│       │   ├── claude_code.py
│       │   ├── opencode.py
│       │   └── codex.py
│       ├── output/
│       │   ├── __init__.py
│       │   ├── json_out.py
│       │   ├── markdown.py
│       │   ├── table.py
│       │   └── github_pr.py    # Post to PR via gh api
│       ├── memory/
│       │   ├── __init__.py
│       │   ├── store.py        # SQLite memory store
│       │   └── feedback.py     # Check reactions/resolutions
│       └── models.py           # Pydantic models (Finding, ReviewResult)
├── action.yml                  # GitHub Action definition
├── Dockerfile                  # For GH Action
└── tests/
```

**Tech stack:** Python 3.14+, uv, Pydantic, click (or typer), aiosqlite, `gh` CLI for GitHub interactions.

## Key Design Decisions

- **Multi-pass over single-pass:** Focused prompts produce more accurate findings per category. Parallel execution keeps wall time reasonable.
- **Delegate to AI CLIs:** No API key management, model selection, or retry logic to build. Users' existing CLI setup (auth, models) works out of the box.
- **Memory via SQLite:** Lightweight, no server, lives in the repo. Feedback collected passively from GitHub reactions at review time.
- **`gh` CLI for GitHub:** No PyGithub/octokit dependency. `gh` handles auth, rate limiting, and API versioning.
- **Clean slate:** No code reused from the previous pipeline harness. Fresh project structure.
