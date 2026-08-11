# Configuration

Superseded looks for `.superseded.yaml` in the repository root. If the file doesn't exist, sensible defaults are used.

## Providers

| Provider | Env var | Default model |
|---|---|---|
| `deepseek` (default) | `SUPERSEDED_DEEPSEEK_API_KEY` | `deepseek-v4-flash` |
| `openai` | `SUPERSEDED_OPENAI_API_KEY` | `gpt-5.6-terra` |
| `anthropic` | `SUPERSEDED_ANTHROPIC_API_KEY` | `claude-sonnet-5` |

Set the env var for the provider you want, then either pass `--provider openai` (or `anthropic`) on the CLI or set `provider:` in `.superseded.yaml`.

## Generating a Config

```bash
# Probe the environment and write a config
superseded init

# Overwrite existing config
superseded init --force
```

`init` is non-interactive. It probes PATH for `gh`, checks for an installed `code-review-graph`, checks for the provider API keys (`SUPERSEDED_DEEPSEEK_API_KEY`, `SUPERSEDED_OPENAI_API_KEY`, `SUPERSEDED_ANTHROPIC_API_KEY`), and writes `.superseded.yaml` with `provider: deepseek`.

## Full Reference

```yaml
# .superseded.yaml — all fields with their defaults

# --- Provider ---
provider: deepseek                       # deepseek | openai | anthropic
model: null                              # null = provider default (deepseek-v4-flash / gpt-5.6-terra / claude-sonnet-5)
reasoning_effort: max                    # low | medium | high | max — mapped per provider (anthropic max → xhigh)

# --- Output ---
format: table                           # table | json | markdown
post_to_pr: false                       # Always post to PR (overridable by --post)
log_format: text                        # text | json — CLI log output format
log_level: WARNING                      # DEBUG | INFO | WARNING | ERROR | CRITICAL

# --- Passes ---
passes:
  security: true
  correctness: true
  performance: true
  style: true
  architecture: true

# --- Memory & Learning ---
memory: true                            # Enable memory/feedback store
progressive: true                       # Incremental review (only new commits)
learned_review: true                    # Adaptive learning from feedback
reflection_threshold: 5                 # Min feedback events before AI reflection
max_learned_rules: 5                    # Max learned rules to inject into prompts

# --- Context Grounding ---
static_analysis: true                   # Run ruff, mypy, eslint, etc.
usage_retrieval: true                   # Cross-file caller search via rg
conventions: true                       # Inject AGENTS.md, CLAUDE.md, etc.
spec_retrieval: true                    # Inject relevant specs & plans
graph: true                             # Use code-review-graph for usage retrieval
verify: true                            # Post-review verification pass (extra API call)
```

## Configuration Precedence

### Provider and Model

`SUPERSEDED_PROVIDER` / `SUPERSEDED_MODEL` / `SUPERSEDED_REASONING_EFFORT` env vars **override everything**. This is designed for CI secrets — set them in your GitHub Action and they cannot be overridden by a config file. (`SUPERSEDED_AGENT` still works as a deprecated alias for `SUPERSEDED_PROVIDER`.)

| Env var | Purpose |
|---|---|
| `SUPERSEDED_PROVIDER` | `deepseek` (default) \| `openai` \| `anthropic` |
| `SUPERSEDED_MODEL` | Override the provider's default model |
| `SUPERSEDED_REASONING_EFFORT` | `low` \| `medium` \| `high` \| `max` (default `max`) |
| `SUPERSEDED_DEEPSEEK_API_KEY` | Key for the `deepseek` provider |
| `SUPERSEDED_OPENAI_API_KEY` | Key for the `openai` provider |
| `SUPERSEDED_ANTHROPIC_API_KEY` | Key for the `anthropic` provider |

The provider requires the matching API key: `SUPERSEDED_DEEPSEEK_API_KEY`,
`SUPERSEDED_OPENAI_API_KEY`, or `SUPERSEDED_ANTHROPIC_API_KEY` — without the
key for the selected provider, `superseded review` fails.

```
environment variable > --provider/--model flag > config file
```

### Graph

`SUPERSEDED_GRAPH` env var (`1`, `true`, `yes`, `on`) overrides the CLI flag, which overrides the config file.

```
SUPERSEDED_GRAPH env var > --graph/--no-graph flag > config file
```

### Logging

`SUPERSEDED_LOG_FORMAT` (`text` | `json`) and `SUPERSEDED_LOG_LEVEL` (any standard level name) env vars override the CLI flags, which override the config file. Server mode defaults to JSON regardless.

```
SUPERSEDED_LOG_FORMAT / SUPERSEDED_LOG_LEVEL env var > --log-format / --log-level flag > config file
```

## Config Fields Explained

### `provider`

Which model provider to use. The provider is called directly over its API — no external CLI is involved. Choices:

| Provider | Auth | Default model |
|---|---|---|
| `deepseek` (default) | `SUPERSEDED_DEEPSEEK_API_KEY` (required) | `deepseek-v4-flash` |
| `openai` | `SUPERSEDED_OPENAI_API_KEY` (required) | `gpt-5.6-terra` |
| `anthropic` | `SUPERSEDED_ANTHROPIC_API_KEY` (required) | `claude-sonnet-5` |

### `model`

The model ID sent with each review prompt. Set to `null` to use the provider's default (`deepseek-v4-flash` for `deepseek`, `gpt-5.6-terra` for `openai`, `claude-sonnet-5` for `anthropic`).

### `reasoning_effort`

Reasoning depth for the selected provider: `low` | `medium` | `high` | `max` (default `max`). Higher effort produces deeper reasoning at the cost of latency and tokens. The value is mapped per provider (e.g. Anthropic maps `max` to `xhigh`). In thinking mode the API ignores `temperature`/`top_p` silently. Override with `--reasoning-effort` or `SUPERSEDED_REASONING_EFFORT`.

### `format`

Output format for findings. `table` is the most human-readable. Use `json` for scripting and piping to `jq`. `markdown` is suitable for pasting into issues or PR bodies.

### `log_format`

Format for **log** output on stderr (separate from `format`, which controls review-findings output on stdout). `text` (default) emits human-readable `LEVEL logger: message` lines; `json` emits one JSON object per line (`event`, `level`, `time`, plus any extra fields). Server mode always uses JSON. Override with `--log-format` or `SUPERSEDED_LOG_FORMAT`.

### `log_level`

Lowest severity of log records that surface on stderr. Defaults to `WARNING`, which keeps the CLI quiet (only progress messages and warnings appear). Lower to `INFO` or `DEBUG` to see what each pass and context source is doing. Override with `--log-level` or `SUPERSEDED_LOG_LEVEL`.

### `post_to_pr`

When `true`, every `superseded review --pr N` also posts inline comments. Equivalent to always passing `--post`. The `--post` flag still works as an override.

### `memory`

When enabled, findings are persisted to `.superseded/memory.db` (SQLite). This database stores:
- All findings ever produced
- User feedback (helpful/dismiss)
- Review watermarks (for progressive review)
- Learned rules from AI reflection

The database is gitignored and should not be committed. Its schema is managed by Alembic migrations, applied automatically on startup; use `superseded migrate` to run or inspect them deliberately.

Disable with `memory: false` or `--no-memory` to run stateless reviews.

### `progressive`

When `true` and memory is enabled, superseded only reviews new commits since the last review. This avoids re-reviewing the entire PR on every push. Requires `memory: true`.

### `learned_review`

When `true` and memory is enabled, superseded builds a learned context from past feedback:
1. Gathers stats on which finding types are accepted or dismissed
2. When feedback events exceed `reflection_threshold`, sends them to the provider to infer team-preference rules
3. Injects the top `max_learned_rules` into future review prompts

Example learned rules:
- "Prefer early returns over nested conditionals"
- "Flag bare except clauses as important, not critical"
- "Accept pattern-matching style findings on domain logic files"

### `reflection_threshold`

Minimum number of **new** feedback events (since the last reflection) before triggering AI reflection. Lower values mean faster adaptation but more token usage.

### `max_learned_rules`

Maximum number of learned rules to inject into a review prompt. Rules are sorted by confidence (descending) then recency.

### `static_analysis`

When enabled, superseded detects available static analysis tools in your project and runs them on changed files only:

| Tool | Detected by | Language |
|---|---|---|
| ruff | `[tool.ruff]` in pyproject.toml | Python |
| mypy | `[tool.mypy]` in pyproject.toml | Python |
| bandit | `[tool.bandit]` in pyproject.toml | Python |
| eslint | `.eslintrc.*` or config file | JS/TS |
| tsc | `tsconfig.json` | TypeScript |
| gofmt | `go.mod` | Go |
| go vet | `go.mod` | Go |
| staticcheck | `go.mod` + `staticcheck` on PATH | Go |
| gitleaks | `.git` directory exists | All |

Results are capped at 4000 characters total.

### `usage_retrieval`

When enabled, superseded extracts changed symbol names from the diff (functions, classes, constants) and searches the repo for callers using `rg` (ripgrep). This gives the model context about who depends on the changed code. Capped at 25 symbols and 6000 characters.

### `conventions`

When enabled, superseded reads project convention files from the repo root and injects them into prompts. Files checked:
- `AGENTS.md`
- `CLAUDE.md`
- `GEMINI.md`
- `CONTRIBUTING.md`
- `.editorconfig`

Sections describing toolchains, environments, commands, and packaging are stripped to keep prompts concise. Capped at 4000 characters.

### `spec_retrieval`

When enabled, superseded searches for design docs relevant to the diff. It checks:
- `docs/superseded/specs/*.md`
- `docs/superseded/plans/*.md`
- `.opencode/skills/**/*.md`
- `.agents/skills/**/*.md`
- `skills/**/*.md`

A doc is considered relevant if its slug appears in a changed file path, or if the doc's body mentions any changed file or path. Results are sorted by modification time and capped at 6000 characters.

### `graph`

When enabled and `code-review-graph` is installed with a built graph at `.code-review-graph/`, superseded uses the graph's in-process `query_graph` API for usage retrieval instead of `rg`. This is faster and provides richer structural context (callers, callees, imports, tests). Falls back to `rg` silently if the graph is unavailable.

To set it up:

```bash
uv add code-review-graph
code-review-graph build
```

### `verify`

When enabled (default) and the run produced findings, superseded makes one extra provider call that double-checks each finding against the diff and drops false positives. Disable with `verify: false`, `--no-verify`, or `SUPERSEDED_VERIFY=0` to save tokens.
