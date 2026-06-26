# AGENTS.md

Superseded is a Python 3.14+ CLI tool that runs 5 parallel AI code-review passes by shelling out to external AI CLIs (claude-code, opencode, codex), then merges/dedupes findings. Source lives under `src/superseded/` (package layout); tests under `tests/`.

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
uv run superseded init                          # detect AI CLIs + write .superseded.yaml
```

There is no CI, no Makefile/Taskfile, and no pre-commit hooks configured. Verifying your work = run ruff check + ruff format + pytest yourself before declaring done.

## Conventions

- Every module starts with `from __future__ import annotations`. Keep this when adding files.
- Ruff rule set is strict: `E,W,F,I,N,UP,B,SIM,TCH,RUF` with `E501,B008,TC001-003,E741` ignored. Line length 100, double quotes, isort with `known-first-party = ["superseded"]`. Match existing style rather than guessing.
- Pydantic v2 models for all data (`models.py`). `Finding.id` is auto-derived in `model_post_init` from pass/file/line/title via SHA-256 — don't set it manually unless you need a stable override.
- pytest is configured with `asyncio_mode = "auto"`: async test functions run without an explicit `@pytest.mark.asyncio`. `pytest-asyncio` is a dev dep.
- The 5 pass names are fixed `Literal`s in `models.py` (`PassName`): `security, correctness, performance, style, architecture`. Adding a pass means updating that Literal, `PassConfig` in `config.py`, and the default list in `review/engine.py`.
- `Config.conventions` and `Config.spec_retrieval` (default `true`) inject repo-grounded convention docs and diff-relevant specs/plans/skills into every pass prompt. Disable with `.superseded.yaml` `conventions: false` / `spec_retrieval: false`, or `--no-conventions` / `--no-specs`. See `context/conventions.py` and `context/spec_retrieval.py`.

## Architecture notes

- Entry point: `superseded = superseded.cli:cli` (a click group). CLI commands are `review` and `feedback` (`src/superseded/cli.py`).
- `review/engine.py` runs passes concurrently via `ThreadPoolExecutor(max_workers=len(passes))`; each pass builds a prompt (`review/prompts.py`) and calls `agent.build_command()` then `subprocess.run` (timeout 300s). Failures in a single pass are logged and skipped, not fatal.
- Agents are pluggable: subclass `agents/base.py:Agent` (implement `name`, `build_command`, `parse_output`) and register in `AGENT_MAP` in `review/engine.py`. Output parsing expects JSON findings usable as `Finding(**item)`.
- `superseded init` is a non-interactive setup command: it probes PATH for the supported AI CLIs (via `src/superseded/detection.py`, which wraps `AGENT_MAP` + `Agent.is_available()`) plus `gh`, picks a default agent + model, and writes a `.superseded.yaml` via `config.write_config`. Refuses to overwrite without `--force`.
- Memory/feedback store is SQLite at `.superseded/memory.db` (`memory/store.py`). `.superseded/` and `*.db` are gitignored — do not commit the DB. The schema self-migrates on `store.init()`.
- Runtime external dependencies an agent typically will not have: the `gh` CLI must be authenticated (used for `gh pr diff`, `gh pr view`, PR comments, feedback reactions) and at least one AI CLI on PATH matching the selected `--agent`. Tests mock these (`tests/test_integration.py`, `tests/test_diff.py`); do not make them hit real `gh` or AI CLIs.

## Configuration precedence

For `agent` and `model`: **env vars > CLI flags > config file**, see `resolve_agent`/`resolve_model` in `cli.py`.
- Env: `SUPERSEDED_AGENT`, `SUPERSEDED_MODEL` — these override flags and config; use them for GitHub Action secrets.
- Config file: `.superseded.yaml` at repo root (optional; defaults in `config.py`).

## Packaging / GitHub Action

- `action.yml` defines a Docker-based Action (`runs.using: docker`, `image: Dockerfile`). `entrypoint.sh` reads `INPUT_AGENT`/`INPUT_MODEL`/`INPUT_PASSES`/`INPUT_POST` and `GITHUB_EVENT_PULL_REQUEST_NUMBER`.
- The Dockerfile builds from `python:3.14-slim`, installs `gh`, and `pip install .` (no uv inside the image). If you change deps in `pyproject.toml`, the Action image rebuild picks them up via `pip install .`.

## Docs

Long-form design lives under `docs/superseded/`:
- `specs/YYYY-MM-DD-<slug>.md` — design specs
- `plans/YYYY-MM-DD-<slug>.md` — implementation plans with `- [ ]` task checkboxes

When implementing a plan, follow its checkbox ordering. `index.html` at repo root is leftover scaffolding and is not part of the Python tool; leave it unless a task says otherwise.

## Gitignore gotchas

`.superseded/` (runtime memory dir), `*.db`/`*.sqlite3`, `.code-review-graph/`, `.ruff_cache/`, `.pytest_cache/`, and `.venv/` are all gitignored. Don't add these to commits.