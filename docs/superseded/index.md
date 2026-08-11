# Superseded — Reviews That Supersede Themselves

Superseded is a CLI tool that runs **five parallel AI code review passes** over your changes, then merges and deduplicates the findings. It calls your choice of DeepSeek, OpenAI, or Anthropic API directly — no external AI CLIs to install or configure. Over time, it **learns** from your feedback — dismissed false positives stop reappearing, and helpful findings get reinforced.

## Why use it?

| Capability | What it does |
|---|---|
| **Five specialised passes** | Security, correctness, performance, style, architecture — each with a focused prompt |
| **Grounded context** | Injects your project conventions, relevant design specs, static analysis results, and cross-file usage into every prompt |
| **Direct API provider** | Reviews run over DeepSeek, OpenAI, or Anthropic — no CLI agents or sandboxes to manage |
| **Progressive review** | On PRs, only reviews new commits since the last pass. No wasted tokens |
| **Adaptive learning** | Tracks which findings you accept or dismiss. Infers team preferences and adjusts future reviews |
| **GitHub integration** | Post inline comments, check reactions for feedback, run as a GitHub Action |
| **Server mode** | Run as a GitHub App that auto-reviews every PR on push |

## Quick Start

```bash
# 1. Install
uv tool install git+https://github.com/fabioesposito/superseded
# or, from a source checkout:
#   git clone https://github.com/fabioesposito/superseded
#   cd superseded && uv sync && uv tool install .

# 2. Set your DeepSeek API key (get one at https://platform.deepseek.com)
export SUPERSEDED_DEEPSEEK_API_KEY=sk-...

# ...or use OpenAI or Anthropic instead
export SUPERSEDED_OPENAI_API_KEY=sk-...    # https://platform.openai.com
export SUPERSEDED_ANTHROPIC_API_KEY=sk-ant-...  # https://console.anthropic.com

# 3. (Optional) Write a config file
superseded init

# 4. Review a PR
superseded review --pr 123

# 5. Review a local diff
superseded review --diff HEAD~3..HEAD

# 6. Review specific files
superseded review src/auth.py src/models.py

# 7. Review uncommitted changes (no args = git diff HEAD; --staged = index only)
superseded review
```

## How it works

```
superseded review --pr 123
│
├─ Fetch diff (gh pr diff, or git diff for local)
├─ Gather grounded context in parallel:
│   ├─ Project conventions (AGENTS.md, CLAUDE.md, etc.)
│   ├─ Design specs & plans relevant to the diff
│   ├─ Static analysis (ruff, mypy, eslint, etc.)
│   ├─ Cross-file usage search (who calls the changed symbols?)
│   └─ Surrounding file context (±20 lines around changes)
├─ Apply past feedback & learned guidelines
├─ Run 5 passes concurrently (ThreadPoolExecutor):
│   ├─ security: injection, auth, secrets, deserialization
│   ├─ correctness: logic errors, nulls, race conditions
│   ├─ performance: N+1 queries, allocations, blocking I/O
│   ├─ style: naming, dead code, complexity, type hints
│   └─ architecture: coupling, contracts, separation of concerns
├─ Merge & deduplicate findings by (file, line, title)
├─ Rank by severity: critical > important > suggestion > nit
├─ Output as table (default), JSON, or markdown
└─ Persist to memory store for future learning
```

## Requirements

- **Python 3.14+**
- **A provider API key** (one of):
  - `SUPERSEDED_DEEPSEEK_API_KEY` — platform.deepseek.com
  - `SUPERSEDED_OPENAI_API_KEY` — platform.openai.com
  - `SUPERSEDED_ANTHROPIC_API_KEY` — console.anthropic.com
- `gh` CLI (optional — required for local PR reviews and feedback; the server uses the GitHub REST API directly)

## Next Steps

- **[Review Guide](review.md)** — all review modes, output formats, passes explained
- **[Configuration](configuration.md)** — every `.superseded.yaml` option
- **[Feedback & Learning](feedback.md)** — how the tool learns from your team
- **[Server Mode](server.md)** — run as a GitHub App or CI Action
