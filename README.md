# Superseded

Multi-pass AI code review tool. Reviews PRs and diffs by delegating to AI CLIs (Claude Code, OpenCode, Codex) with focused prompts per review category.

## How It Works

```
PR or diff
    │
    ▼
5 parallel review passes
├── Security     — injection, auth bypass, secrets, XSS
├── Correctness  — logic bugs, edge cases, error handling
├── Performance  — N+1 queries, blocking I/O, algorithmic issues
├── Style        — naming, dead code, complexity
└── Architecture — coupling, API contracts, separation of concerns
    │
    ▼
Structured findings (JSON / markdown / table)
    │
    ▼
Optional: post as GitHub PR review comments
```

The tool learns from feedback — it tracks which review comments humans dismiss and adjusts future reviews.

## Install

```bash
pip install superseded
```

Or from source:

```bash
git clone https://github.com/fabioesposito/superseded
cd superseded
uv sync
```

## Usage

### CLI

```bash
# Review a PR
superseded review --pr 123

# Review a local diff
superseded review --diff HEAD~3..HEAD

# Choose agent + model
superseded review --pr 123 --agent claude-code --model claude-sonnet-4-20250514

# Output format
superseded review --pr 123 --format json
superseded review --pr 123 --format markdown
superseded review --pr 123 --format table  # default

# Post to GitHub PR
superseded review --pr 123 --post

# Selective passes
superseded review --pr 123 --passes security,correctness
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
      - uses: fabioesposito/superseded@v1
        with:
          agent: claude-code
          model: claude-sonnet-4-20250514
          passes: security,correctness,performance
          post: true
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

### Feedback

```bash
# Dismiss a finding (won't appear in future reviews)
superseded feedback <comment-id> --dismiss

# Mark as helpful
superseded feedback <comment-id> --helpful
```

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

## Supported Agents

| Agent | Invocation | Auth |
|-------|-----------|------|
| **claude-code** | `claude -p --bare --model` | `ANTHROPIC_API_KEY` |
| **opencode** | `opencode run` | Provider-specific |
| **codex** | `codex exec --json --model` | `CODEX_API_KEY` |

## Output Formats

### Table (default)
```
Sev          Pass           File                           Line   Title
---------------------------------------------------------------------------
🔴 critical  security       src/auth.py                    42     SQL injection
🟡 suggestion style         src/utils.py                   15     Unclear naming

Total: 2 findings
  critical: 1
  suggestion: 1
```

### JSON
```json
[
  {
    "severity": "critical",
    "pass_name": "security",
    "file": "src/auth.py",
    "line": 42,
    "end_line": 45,
    "title": "SQL injection in user query",
    "description": "User input interpolated into SQL string",
    "suggestion": "Use parameterized queries"
  }
]
```

### Markdown
Grouped by severity with inline code blocks and suggestions.

## Requirements

- Python 3.14+
- At least one AI CLI installed (claude-code, opencode, or codex)
- `gh` CLI for GitHub PR interaction

## Tests

```bash
uv run pytest tests/ -v
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

## License

MIT
