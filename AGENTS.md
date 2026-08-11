# AGENTS.md

Superseded is a Python 3.14+ CLI tool that runs 5 parallel AI code-review passes via a direct DeepSeek API call, then merges/dedupes findings. Source lives under `src/superseded/` (package layout); tests under `tests/`.

## Toolchain & environment

- Python **>=3.14** is required (`requires-python`, ruff `target-version = "py314"`). The system `python` may be 3.13 and will fail; always invoke via uv: `uv run python`, `uv run pytest`, `uv run ruff`.
- Package manager is **uv** (`uv.lock` is committed). First run: `uv sync`. Never edit `uv.lock` by hand; change deps in `pyproject.toml` then `uv lock`/`uv sync`.
- The venv is managed by uv (`.venv/`, gitignored). Do not use system pip.

## Commands (run from repo root)

```bash
uv sync                                    # install/lock deps
uv run pytest tests/ -v                    # full test suite
uv run pytest tests/test_diff.py -v        # one file
uv run pytest "tests/test_diff.py::test_parse_diff_files" -v   # one test
uv run ruff check src/ tests/             # lint
uv run ruff format src/ tests/            # format
uv run superseded review --diff HEAD~1..HEAD --format json   # run the tool locally
uv run superseded init                          # probe gh/DeepSeek key/graph + write .superseded.yaml
```

There is no CI, no Makefile/Taskfile, and no pre-commit hooks configured. Verifying your work = run ruff check + ruff format + pytest yourself before declaring done.

## Conventions

- Every module starts with `from __future__ import annotations`. Keep this when adding files.
- Ruff rule set is strict: `E,W,F,I,N,UP,B,SIM,TCH,RUF` with `E501,B008,TC001-003,E741` ignored. Line length 100, double quotes, isort with `known-first-party = ["superseded"]`. Match existing style rather than guessing.
- Pydantic v2 models for all data (`models.py`). `Finding.id` is auto-derived in `model_post_init` from pass/file/line/title via SHA-256 — don't set it manually unless you need a stable override.
- pytest is configured with `asyncio_mode = "auto"`: async test functions run without an explicit `@pytest.mark.asyncio`. `pytest-asyncio` is a dev dep.
- The 5 pass names are fixed `Literal`s in `models.py` (`PassName`): `security, correctness, performance, style, architecture`. Adding a pass means updating that Literal, `PassConfig` in `config.py`, and the default list in `review/engine.py`.
- `Config.conventions` and `Config.spec_retrieval` (default `true`) inject repo-grounded convention docs and diff-relevant specs/plans/skills into every pass prompt. Disable with `.superseded.yaml` `conventions: false` / `spec_retrieval: false`, or `--no-conventions` / `--no-specs`. See `context/conventions.py` and `context/spec_retrieval.py`.
- `Config.graph` (default `true`) routes usage retrieval through `code-review-graph` when installed and a built graph exists at `.code-review-graph/`; otherwise the rg path in `context/usage_retrieval.py` runs unchanged. Toggle precedence mirrors provider/model: `SUPERSEDED_GRAPH` env > `--graph`/`--no-graph` flag > config file. Install the optional dep with `uv sync --extra graph` (or `uv add code-review-graph`) then `code-review-graph build`.
- **`except A, B:` is intentional, do not "fix" it.** Several modules use the comma form (e.g. `except ValueError, TypeError:` in `server/worker.py`, `context/static_analysis.py`, `diff.py`, `cli.py`, `output/github_pr.py`). On Python 3.14 this compiles as a tuple match — i.e. `except (A, B):` — and correctly catches both types (verified via `dis`: `BUILD_TUPLE 2; CHECK_EXC_MATCH`). It is **not** the Python-2 `except A as B` form and **not** a bug. Leave it as-is; do not open issues or send PRs parenthesizing these.

## Architecture notes

- Entry point: `superseded = superseded.cli:cli` (a click group). CLI commands are `review` and `feedback` (`src/superseded/cli.py`).
- `review/engine.py` runs passes concurrently via `ThreadPoolExecutor(max_workers=len(passes))`; each pass builds a prompt (`review/prompts.py`) and calls `provider.complete()` via the provider SDKs (`openai` SDK for deepseek/openai, `anthropic` SDK for anthropic) (timeout 600s, `DEFAULT_PASS_TIMEOUT`). Failures in a single pass are logged and skipped, not fatal. A pass whose returned findings fail `Finding()` validation (e.g. `severity: "minor"`) is retried **once** with a corrective prompt (`prompts.build_retry_prompt`) before the malformed items are dropped — `run_pass` calls the provider at most twice per pass. Cross-pass dedup (`review/merger.py`) collapses findings sharing `Finding.dedup_key` (file+line+title) and keeps the **highest-severity** one, not the first seen. In the local CLI path, a run with any skipped pass exits `EXIT_PARTIAL_FAILURE` (3) so CI can distinguish infra degradation from a clean review (0); the server path surfaces the same via the check-run conclusion instead.
- Providers are pluggable: subclass `providers/base.py:Provider` (implement `name`, `complete`), register in `PROVIDER_MAP` in `providers/__init__.py` (deepseek: `DeepSeekProvider`, openai: `OpenAIProvider` (Responses API), anthropic: `AnthropicProvider` (Messages API)). `complete()` returns a `ProviderResponse(content, prompt_tokens, completion_tokens, model, raw)`. The engine parses `content` via `providers/parsing.parse_findings_json`, which returns a list of dicts usable as `Finding(**item)`.
- `superseded init` is a non-interactive setup command: it probes PATH for `gh`, checks for the provider API keys (`SUPERSEDED_DEEPSEEK_API_KEY` / `SUPERSEDED_OPENAI_API_KEY` / `SUPERSEDED_ANTHROPIC_API_KEY`), checks for an installed `code-review-graph` at `.code-review-graph/`, and writes a `.superseded.yaml` via `config.write_config`. Refuses to overwrite without `--force`.
- Memory/feedback store is SQLite at `.superseded/memory.db` (`memory/store.py`). `.superseded/` and `*.db` are gitignored — do not commit the DB. The schema self-migrates on `store.init()`.
- Memory store has two interchangeable backends behind the `Store` Protocol in `memory/backend.py`: `MemoryStore` (SQLite, default, used by the local CLI path) and `PostgresStore` (asyncpg pool, server-only, selected via `ServerConfig.database_url`). `make_store(database_url)` dispatches on URL scheme (`sqlite://`/empty → SQLite, `postgres(ql)://` → Postgres). Postgres tests in `tests/test_postgres_store.py` are `@pytest.mark.postgres` and skipped unless `SUPERSEDED_POSTGRES_TEST_DSN` is set; `addopts = "-m 'not postgres'"` keeps the default `uv run pytest` green without a live DB.
- Runtime external dependencies an agent typically will not have: the `gh` CLI must be authenticated (used for `gh pr diff`, `gh pr view`, PR comments, feedback reactions) and one of `SUPERSEDED_DEEPSEEK_API_KEY`, `SUPERSEDED_OPENAI_API_KEY`, or `SUPERSEDED_ANTHROPIC_API_KEY` must be set for the selected provider. Tests mock these (`tests/test_integration.py`, `tests/test_diff.py`); do not make them hit real `gh` or the provider APIs.
- Usage retrieval has two interchangeable paths: the default rg-based `context/usage_retrieval.py` (calls `rg` over the repo for changed symbols) and the graph-grounded `context/graph_retrieval.py` (queries `code-review-graph`'s in-process `query_graph` for callers of those symbols). `context/gathering.py` picks at runtime based on `is_available(root)` and the resolved `graph` toggle. Refresh-before-query (`code-review-graph update --brief`) runs in the same worker thread as the query so it always completes first; both run in parallel with the other context futures.

## Configuration precedence

For `provider` and `model`: **env vars > CLI flags > config file**, see `resolve_provider`/`resolve_model` in `cli.py`.
- Env: `SUPERSEDED_PROVIDER` (`SUPERSEDED_AGENT` still works as a deprecated alias — it emits a `DeprecationWarning`), `SUPERSEDED_MODEL`, `SUPERSEDED_REASONING_EFFORT` — these override flags and config. One of `SUPERSEDED_DEEPSEEK_API_KEY`, `SUPERSEDED_OPENAI_API_KEY`, or `SUPERSEDED_ANTHROPIC_API_KEY` is required for the selected provider.
- Config file: `.superseded.yaml` at repo root (optional; defaults in `config.py`). Legacy `agent:` / `sandbox:` keys are handled in `load_config` (hard error for the old CLI-agent names, warning otherwise).

## Packaging / GitHub Action

- `action.yml` is a **composite** Action (`runs.using: composite`) — a single `curl` step that POSTs `{owner, repo, pr_number, passes?}` to a running review server at `$SUPERSEDED_SERVER_URL/review/pr` (env `SUPERSEDED_SERVER_URL`/`SUPERSEDED_SERVER_KEY` override the `server-url`/`server-key` inputs). The Action no longer builds a Docker image or runs agents; the server (started via `superseded serve`) calls the configured provider API directly (deepseek/openai/anthropic) and posts the review via its GitHub App, which must be installed on the repo. The server requires the API key matching its `provider:` in its environment (it refuses to start without it); the Action never receives the key.
- The Dockerfile (`docker/Dockerfile`, multi-target `base`/`cli`/`api`) builds from `python:3.14-slim`; the `cli` target installs `gh` and `pip install .` for containerized CLI use, the `api` target runs the server. The Action references neither. `docker/entrypoint.sh` was removed.

## Docs

Long-form design lives under `docs/superseded/`:
- `specs/YYYY-MM-DD-<slug>.md` — design specs
- `plans/YYYY-MM-DD-<slug>.md` — implementation plans with `- [ ]` task checkboxes

When implementing a plan, follow its checkbox ordering. `index.html` at repo root is leftover scaffolding and is not part of the Python tool; leave it unless a task says otherwise.

**CRITICAL — superseded ≠ superpowers:** These are two completely separate projects. `docs/superseded/` is for this repo's design docs (the AI code-review tool). `docs/superpowers/` is for an unrelated project and MUST NOT be created or committed here. If you see `docs/superpowers/` or any file referencing "superpowers" skills/landing-pages/logos, delete it immediately. Likewise, do not copy superseded docs into the superpowers repo.

## Gitignore gotchas

`.superseded/` (runtime memory dir), `*.db`/`*.sqlite3`, `.code-review-graph/`, `.ruff_cache/`, `.pytest_cache/`, and `.venv/` are all gitignored. Don't add these to commits. `.code-review-graph/` (CRG's local SQLite graph DB and artifacts) is gitignored. Don't commit it.

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
|------|----------|
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.
