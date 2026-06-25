# Project Conventions & Repo-Spec Enforcement — Design

Date: 2026-06-25
Status: Draft (pending user review)
Scope: Enrich every per-pass review prompt with two new curated, repo-grounded context blocks: (1) **Project Conventions** — auto-discovered from root-level convention docs (`AGENTS.md`/`CLAUDE.md`/`GEMINI.md`/`CONTRIBUTING.md`/`.editorconfig`) with non-convention sections stripped; (2) **Relevant Design Specs & Plans** — auto-discovered from `docs/superseded/specs/`, `docs/superseded/plans/`, and skill-definition files inside the repo, filtered to those relevant to the diff. The prompts gain explicit enforcement rules so the AI flags deviations from these as findings at calibrated severity.

Companion to the grounded-review-context pattern (`docs/superseded/specs/2026-06-24-grounded-review-context-design.md`): this spec adds two more blocks using the same `context/` → `cli.py`/`server/worker.py` → `engine.review()` → `build_prompt()` pipeline.

## Motivation

Superseded's per-pass prompts (`prompts.py:48`) currently give the AI generic focus areas and a generic Rules block, but **no knowledge of the reviewed repo's actual conventions**. Consequences:

- The `style` and `architecture` passes apply generic preferences and miss repo-specific rules — e.g. "every module starts with `from __future__ import annotations`", "line length 100, double quotes, isort with `known-first-party = ['superseded']`", "Pydantic v2 models for all data". The AI has no way to know these are binding.
- The AI can also *mis-flag* conforming code — e.g. flagging double-quoted strings in a repo that mandates double quotes — because it falls back to its training-data defaults.
- The `architecture` and `correctness` passes ask "does the code match the PR description?" but never see the repo's own design specs or implementation plans, even though those live in-tree under `docs/superseded/`. A change that contradicts a committed spec goes uncaught.
- Skill definitions inside the repo (`.opencode/skills/`, `.agents/skills/`, `skills/`) encode behavioral contracts that code under review may be expected to follow; these are invisible to the AI pass.

The grounded-review-context spec established that *hybrid AI = a deterministic pipeline feeding curated context to an agent*. This spec extends that pipeline with two more curated blocks, both deterministic and repo-grounded, and makes the agent **enforce** them rather than merely read them.

## Design choices (decided)

| Decision | Choice | Rejected alternative | Rationale |
|---|---|---|---|
| Convention source | Auto-discover text docs only | Auto-discover + parse `pyproject.toml` `[tool.ruff]`/`[tool.mypy]`; user-authored `guidelines:` | The static-analysis pass already runs ruff/mypy/bandit over changed files (`context/static_analysis.py`), so re-parsing their config duplicates that signal. Text docs carry prose rules linters can't express ("never log secrets", "Pydantic v2 for all data"). Auto-discovery gives zero-config first run, matching the established pattern. User-authored override deferred (YAGNI). |
| Pass targeting | All 5 passes receive the conventions block | Style + architecture only; tiered (short for all, full for style/arch) | Conventions can include security-relevant rules ("never log secrets") the security pass must see, and correctness-relevant rules ("Pydantic v2 models for all data") the correctness pass must see. Tiering adds complexity for marginal token savings; the budget cap bounds cost. |
| Enforcement strength | Hard enforce, calibrated severity | Soft (context only); hard enforce with flat severity | The user asked for *enforcement*, not passive context. Calibrated severity (`nit`/`suggestion` default; `important` only when the deviation breaks correctness/security) avoids a flood of nits while still surfacing real deviations. Flat severity would duplicate the static-analysis pass for ruff-covered rules and noise-flood the review. |
| Doc extraction | Whole-doc minus blocklisted sections | Whole-doc inject + budget cap; section extraction by heading allowlist | Mixed-content docs like `AGENTS.md` interleave conventions with toolchain/packaging/gitignore meta that isn't a coding rule and wastes tokens. A blocklist (drop `Toolchain`, `Environment`, `Commands`, `Packaging`, `GitHub Action`, `Gitignore`, `Docs`) keeps the rest without requiring a `## Conventions` heading to exist — works on docs that don't follow that structure. Heading allowlist is more brittle. |
| Repo docs scope | Include `docs/superseded/specs/`, `docs/superseded/plans/`, and skill definitions, filtered to diff-relevant | Root convention files only (defer specs to v2); include all specs/plans budget-capped | Specs/plans encode intended architecture and intent that strengthen the `architecture` and `correctness` passes. The `correctness` pass already asks "does the code match the PR description?" — seeing the spec that motivated the change lets it answer that against authoritative intent, not just the PR body. Filtering to diff-relevant bounds tokens and keeps signal high. Deferring loses the biggest grounding win. |
| Relevance match | Filename/slug match in body | Symbol match; both filename + symbol; basename-only | A doc is relevant if any changed-file path or basename appears in its body, or the doc's slug (date prefix and `-design`/`-implementation` suffix stripped) appears as a path component in any changed file. Deterministic, `rg`-based, no token cost. Symbol match adds recall but risks false positives on common names; basename-only is too conservative (misses a spec about `prompts.py` that only says "the prompt module"). |
| Block ordering | Conventions first, then Specs, both before `### PR Description` | Specs before Conventions; Conventions as top-level `##` section | Conventions are the binding coding rules the AI must enforce — they belong first so they frame the whole review. Specs are supporting intent. Placing both before `### PR Description` keeps `## Context` as the single enrichment zone, matching the grounded-review spec's additive-section pattern and minimizing the diff to `prompts.py`. |

## Architecture

### Where enrichment lives

Two new pure-function modules in the existing `src/superseded/context/` package (`static_analysis.py` and `usage_retrieval.py` already live there). `cli.py` and `server/worker.py` — which already call `compute_file_context()`, `run_static_analysis()`, `retrieve_usages()` — call the two new functions in the same place and thread the resulting strings through `engine.review()` → `build_prompt()` as two new optional kwargs.

```
cli.py / server/worker.py
  ├─ fetch_diff()                                 [existing]
  ├─ parse_diff_files(diff)                       [existing, reused] → changed_files
  ├─ compute_file_context(diff, root)             [existing]
  ├─ run_static_analysis(changed_files, root)     [existing]
  ├─ retrieve_usages(diff, root)                  [existing]
  ├─ conventions = discover_conventions(root)               [new]
  ├─ spec_signals = discover_repo_specs(diff, root)         [new]
  └─ engine.review(..., conventions_signals=, spec_signals=)
                                                          │
                                                          ▼
build_prompt(pass_name, diff, ..., conventions_signals, spec_signals)
   inserts two new ### sections at the top of ## Context
```

Nothing else changes: `Agent`, `ReviewEngine.review` (still a pass fan-out via `ThreadPoolExecutor`), `merger`, `MemoryStore` are untouched. The two new kwargs are additive and default to `None`, so the prompt degrades gracefully to its current shape when discovery finds nothing or when the features are disabled.

### Why two modules, not one

Considered a single `discover_all_context()` that returns both blocks. Rejected because (a) the two blocks have different inputs (`discover_conventions` needs only `root`; `discover_repo_specs` needs `diff` + `root`) and different failure modes; (b) the existing `context/` modules are each single-purpose (`static_analysis.py`, `usage_retrieval.py`), and matching that shape keeps the package uniform; (c) two modules = two independent config toggles and two independent `--no-<x>` flags, matching the granularity users already have for static/usage/memory.

### Why no `ReviewContext` dataclass

Same reasoning as the grounded-review spec: the loose-kwargs style matches `diff.py` and `engine.review` already; bundling would be a signature-breaking refactor for no current payoff. The two new kwargs bring `build_prompt` to nine parameters total (four with defaults), which is the threshold to reconsider — but not in this spec.

## Module 1 — `context/conventions.py`

### `discover_conventions(root: Path) -> str | None`

1. **Discovery**: non-recursive, case-insensitive scan of `root` for these filenames: `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `CONTRIBUTING.md`, `.editorconfig`. Only files that exist and are readable are included.
2. **Stripping** (markdown docs only — anything ending in `.md`): parse the doc into heading-delimited sections (a section starts at a `#`-prefixed line and runs until the next heading of any level). Drop any section whose heading text contains a case-insensitive substring from `BLOCKLIST`:
   ```
   BLOCKLIST = ["toolchain", "environment", "commands", "packaging",
                "github action", "gitignore", "docs"]
   ```
   Substring match handles `## Toolchain & environment`, `### Commands (run from repo root)`, `## Packaging / GitHub Action`, `## Gitignore gotchas`, `## Docs`. Keeps `## Conventions`, `## Architecture notes`, `## Configuration precedence`, `## Memory/feedback store`, etc. Stripping is per-section: a heading match drops that section's body up to the next heading only.
3. **`.editorconfig`** is injected whole — it's small, structured, and entirely convention-relevant.
4. **Concatenation**: fixed order (AGENTS → CLAUDE → GEMINI → CONTRIBUTING → .editorconfig), each prefixed with a `## <filename>` header line so the AI knows the source. Blank line between docs.
5. **Budget**: `CONVENTIONS_BUDGET = 4000` chars. Truncate the aggregate at the budget; append a tail `… ({N} more chars omitted by conventions budget)` where `N` is the omitted char count. Enforced at concat time inside the module (not in the prompt), matching the grounded-review spec's pattern.
6. **Return `None`** when no convention docs are found → the prompt section renders its placeholder.

### Heading parsing — minimal

No markdown library dependency. A line is a heading iff it matches `^(#{1,6})\s+(.+?)\s*$`. Section bodies are everything between one heading line and the next heading line (any level). This is sufficient for the blocklist substring match and handles the real docs in this repo (`AGENTS.md`, `README.md`).

## Module 2 — `context/spec_retrieval.py`

### `discover_repo_specs(diff: str, root: Path) -> str | None`

1. **Discovery**: scan these globs relative to `root`, each independently (any subset may exist):
   - `docs/superseded/specs/*.md`
   - `docs/superseded/plans/*.md`
   - `.opencode/skills/**/*.md`
   - `.agents/skills/**/*.md`
   - `skills/**/*.md`
   Skills are recursive (`**`) because skill packages nest; specs/plans are flat by existing convention (`docs/superseded/specs/YYYY-MM-DD-<slug>.md`).
2. **Slug derivation**: from a filename like `2026-06-25-grounded-review-context-design.md`, strip the leading date (`YYYY-MM-DD-`) and trailing `-design`/`-implementation`/`-plan` suffix, yielding `grounded-review-context`. For skill files, the slug is the filename without extension.
3. **Relevance filter** (filename/slug match in body): a doc is relevant iff **either**
   - any changed-file path **or basename** appears as a substring of the doc's body, **or**
   - the doc's slug appears as a path component in any changed file (i.e. some changed path contains `/<slug>/` or equals `<slug>` or ends with `/<slug>`).
   Changed files come from `parse_diff_files(diff)` (`diff.py:77`), which returns repo-relative paths (e.g. `src/superseded/review/prompts.py`) — so "changed-file path" is the repo-relative path and "basename" is its final component (`prompts.py`). Matching is case-sensitive for paths (filesystems vary; safe default), case-insensitive for the slug-vs-path-component check (slugs are lowercase by convention).
4. **Concatenation**: specs first (sorted by mtime, newest first — newest specs reflect current intent), then plans (same order), then skills. Each prefixed `## <relative path from root>` so the AI can cite the source. Blank line between docs.
5. **Budget**: `SPEC_BUDGET = 6000` chars (larger than conventions because specs are the high-signal grounding for architecture/correctness and each doc is bigger). Truncate tail with `… ({N} more chars omitted by spec-retrieval budget)`.
6. **Return `None`** when no relevant docs found, when `docs/superseded/` and skill dirs are all absent, or when `rg` is missing → prompt placeholder renders.

### `rg` invocation

Reuse the missing-binary handling from `usage_retrieval.py`: wrap `subprocess.run` in `try/except FileNotFoundError`; on miss, `logger.warning("ripgrep not on PATH, skipping spec retrieval")` and return `None`. Never fatal — conventions can still ship without specs.

For the body-match step, run one `rg` call per candidate doc with an alternation pattern built from the changed-file paths and basenames (e.g. `rg --fixed-strings -e 'src/superseded/review/prompts.py' -e 'prompts.py' <doc>`). The candidate set is small (the spec/plan dirs in this repo hold ~6-7 files each), so N calls is acceptable. If the candidate set grows large, switch to one `rg` call scanning all candidate bodies at once with `--files-with-matches` — noted as a future optimization, not in scope here. The slug-as-path-component check is done in Python against `parse_diff_files` output (no `rg` needed).

## Prompt changes — `prompts.py:build_prompt`

### New signature

```python
def build_prompt(
    pass_name: str,
    diff: str,
    pr_description: str | None,
    file_context: str | None,
    memory_context: str | None,
    static_signals: str | None = None,
    usage_signals: str | None = None,
    conventions_signals: str | None = None,
    spec_signals: str | None = None,
) -> str:
```

### New `### ` sections in `## Context`

Two new subsections inserted as the **first** entries in `## Context`, before `### PR Description`, in this order:

```
## Context

### Project Conventions
{conventions_signals or "No project conventions discovered."}

### Relevant Design Specs & Plans
{spec_signals or "No relevant specs/plans found."}

### PR Description
...
```

The existing `### Changed Files (diff)`, `### Static analysis signals`, `### Cross-file usages`, `### File Context`, `### Past Feedback` sections keep their current order after `### PR Description`. The two new sections sit before everything else in `## Context` because conventions frame the whole review and specs frame intent — both are higher-level than the diff itself.

### Rules block changes

Add two rules to the `## Rules` block (`prompts.py:69`), and amend the existing "only genuine issues" rule:

- Amend existing: *"Only report genuine issues, not style preferences — **except deviations from the Project Conventions below, which are reportable.**"*
- New: *"Enforce the Project Conventions listed below: flag deviations as findings. Use severity `nit`/`suggestion` by default; use `important` only when the deviation breaks correctness or security. Do not flag code that conforms to the conventions."*
- New: *"Use the Relevant Design Specs & Plans as authoritative intent. If changed code contradicts a spec, flag it at severity `important` or higher, citing the spec path."*

`JSON_FORMAT_INSTRUCTIONS` (`prompts.py:26`) and the per-pass role block (`PASS_INSTRUCTIONS`, `prompts.py:3`) are **untouched** — the agent's job description and output shape don't change, only its grounding and one enforcement rule.

### Graceful degradation

Both new kwargs default to `None` and render the placeholder via the existing `ctx or "No … available."` idiom (`prompts.py:56`). When both are `None`, the prompt is identical to today's plus two short placeholder lines — agents that fail to parse the new sections degrade to the old behavior since the sections are additive and the rules still apply only when conventions are present (the amended rule's "except deviations from the Project Conventions below" clause is a no-op when the placeholder is shown).

## Config — `config.py`

`Config` gains two plain bools, mirroring `static_analysis`/`usage_retrieval`:

```python
conventions: bool = True
spec_retrieval: bool = True
```

`.superseded.yaml` can disable either:

```yaml
conventions: false
spec_retrieval: false
```

No CLI flags beyond `--no-conventions`/`--no-specs` (see below). The `is_pass_enabled` pattern is intentionally not reused — these are plain on/off toggles, checked directly in `cli.py`/`server/worker.py` with `if config.conventions and not no_conventions:`, matching `cli.py:272-273`.

## CLI — `cli.py`

Two new flags alongside the existing `--no-static`/`--no-usage`/`--no-memory` (`cli.py:167-169`):

```python
@click.option("--no-conventions", is_flag=True, help="Disable project conventions injection")
@click.option("--no-specs", is_flag=True, help="Disable design spec/plan retrieval")
```

In the review body (`cli.py:264-280`), after the existing context steps:

```python
enable_conventions = config.conventions and not no_conventions
enable_specs = config.spec_retrieval and not no_specs
conventions_signals: str | None = None
spec_signals: str | None = None
if enable_conventions:
    conventions_signals = discover_conventions(root)
if enable_specs:
    spec_signals = discover_repo_specs(diff, root)
```

Threaded into `engine.review(..., conventions_signals=conventions_signals, spec_signals=spec_signals)` at `cli.py:293-303`.

## Server worker — `server/worker.py`

`server/worker.py:190-211` gains the same two calls (gated on `config.conventions`/`config.spec_retrieval`, no `--no-<x>` flags here since the server has no CLI) and threads the results into `engine.review(...)` at `worker.py:205-211`. Mirrors the existing static/usage handling already present in the worker.

## Engine — `review/engine.py`

`ReviewEngine.review` (`engine.py:90`) gains two optional kwargs `conventions_signals: str | None = None`, `spec_signals: str | None = None` and forwards them to `build_prompt(...)` inside the per-pass loop (`engine.py:120-128`). No other change — the fan-out, `ThreadPoolExecutor`, failure handling, and merge are untouched.

## Failure handling summary

| Fault | Module | Behavior |
|---|---|---|
| No convention docs at repo root | conventions | Return `None` → prompt renders `"No project conventions discovered."` placeholder |
| Convention doc unreadable (permissions) | conventions | `logger.warning`, skip that doc, continue with others, never fatal |
| `docs/superseded/` absent | spec_retrieval | That glob contributes no candidates; if all globs are empty, return `None` → placeholder |
| No specs/plans relevant to the diff | spec_retrieval | Return `None` → prompt renders `"No relevant specs/plans found."` placeholder |
| `rg`/ripgrep not on PATH | spec_retrieval | `logger.warning("ripgrep not on PATH, skipping spec retrieval")`, return `None`, never fatal — conventions can still ship |
| Read error on a spec/plan/skill | spec_retrieval | `logger.warning`, skip that doc, continue, never fatal |
| Slug derivation edge case (no date prefix) | spec_retrieval | Use the whole filename stem as the slug; relevance match still works |

All failures are non-fatal, matching the grounded-review spec's failure-handling philosophy and the existing engine precedent of skipping failed passes (`engine.py:136-141`).

## Testing plan

### New: `tests/test_context_conventions.py`

- **Discovery truth-table**: parametrized fixture repos — one with each combo of `{AGENTS.md, CLAUDE.md, GEMINI.md, CONTRIBUTING.md, .editorconfig}` present/absent (case-variant filenames too: `agents.md`, `Agents.md`) → assert `discover_conventions` returns the right subset and `None` when empty.
- **Blocklist stripping**: a canned `AGENTS.md` fixture with sections `## Toolchain & environment`, `## Commands (run from repo root)`, `## Conventions`, `## Architecture notes`, `## Packaging / GitHub Action`, `## Gitignore gotchas`, `## Docs`, `## Configuration precedence` → assert stripped output contains `Conventions`, `Architecture notes`, `Configuration precedence` and does **not** contain `Toolchain`, `Commands`, `Packaging`, `GitHub Action`, `Gitignore`, `Docs`. Assert section bodies under a blocklisted heading are dropped (not just the heading line).
- **`.editorconfig` whole-inject**: fixture with `.editorconfig` only → assert it appears whole, prefixed `## .editorconfig`.
- **Concatenation order**: fixture with all five docs → assert order is AGENTS → CLAUDE → GEMINI → CONTRIBUTING → .editorconfig, each prefixed with its `## <filename>` header.
- **Budget tail**: fixture producing > 4000 chars → assert truncation at `CONVENTIONS_BUDGET` and that the tail includes the omitted-char count.
- **`None` when empty**: no convention docs at root → assert returns `None`.
- **Read-error path**: monkeypatch `Path.read_text` to raise `PermissionError` for one doc → assert that doc is skipped, a warning is logged (use `caplog`), and other docs still appear.

### New: `tests/test_context_spec_retrieval.py`

- **Slug derivation**: parametrized filenames (`2026-06-25-grounded-review-context-design.md` → `grounded-review-context`, `2026-06-24-todo-fixes.md` → `todo-fixes`, `my-skill.md` → `my-skill`) → assert the derived slug.
- **Relevance — filename-in-body**: fixture with two specs, one whose body mentions `src/superseded/review/prompts.py` and one that doesn't; canned diff changing `src/superseded/review/prompts.py` → assert only the first is selected.
- **Relevance — basename-in-body**: spec body mentions `prompts.py` (not the full path); diff changes `src/superseded/review/prompts.py` → assert selected (basename match).
- **Relevance — slug-in-path**: spec slug `grounded-review-context`; diff changes `src/superseded/grounded-review-context/foo.py` (hypothetical) → assert selected via slug-as-path-component. Negative: diff changes unrelated file → assert not selected.
- **Ordering**: multiple relevant specs → assert specs before plans, newest-mtime first within each group.
- **Budget tail**: enough relevant docs to exceed `SPEC_BUDGET` → assert truncation tail and omitted count.
- **`None` when no `docs/` dir**: root with no `docs/superseded/` and no skill dirs → assert returns `None`.
- **`rg` missing**: monkeypatch `subprocess.run` to raise `FileNotFoundError` → assert returns `None` and warns (use `caplog`).
- **Skill discovery**: fixture with `.agents/skills/foo/SKILL.md` whose body mentions a changed file → assert selected and prefixed `## .agents/skills/foo/SKILL.md`.

### Extended: `tests/test_prompts.py`

- **New sections present when kwargs non-empty**: `conventions_signals="## AGENTS.md\n..."`, `spec_signals="## docs/.../spec.md\n..."` → assert `### Project Conventions` and `### Relevant Design Specs & Plans` present and content lands.
- **New sections absent (placeholder) when `None`**: assert `"No project conventions discovered."` and `"No relevant specs/plans found."` placeholders render.
- **Section ordering**: assert `### Project Conventions` < `### Relevant Design Specs & Plans` < `### PR Description` < `### Changed Files (diff)` < `### Static analysis signals` < `### Cross-file usages` < `### File Context` < `### Past Feedback`.
- **Enforcement rules present**: assert the amended "except deviations from the Project Conventions" clause, the "Enforce the Project Conventions" rule, and the "authoritative intent" rule all appear in the Rules block.
- **Regression — old prompt unchanged when both new kwargs `None`**: build a prompt with `conventions_signals=None, spec_signals=None` and assert it equals the pre-change prompt string plus the two placeholder lines (snapshot or explicit equality).

### Extended: `tests/test_integration.py`

- **Signals land in the agent prompt**: monkeypatch `discover_conventions`/`discover_repo_specs` to canned strings; run the review flow; assert the canned strings appear in the `prompt` argument passed to `agent.build_command` (via the existing mock pattern).
- **`--no-conventions` / `--no-specs`**: assert the corresponding discover functions are not called and the prompt contains the placeholder sections.
- **Config false**: `Config(conventions=False)` / `Config(spec_retrieval=False)` → assert functions not called + placeholders render.

## Out of scope (explicit)

- Parsing `pyproject.toml` `[tool.ruff]`/`[tool.mypy]` — the static-analysis pass already runs those linters over changed files; re-parsing their config duplicates that signal.
- User-authored `guidelines:`/`guidelines_file:` override in `.superseded.yaml` — auto-discovery + blocklist is enough for v1; can be added if a repo needs to inject conventions that don't live in a discoverable doc.
- Recursive convention-doc search (only repo root).
- AST/symbol-based spec relevance — filename/slug match is the v1 heuristic; symbol match is a future recall booster.
- Whole-repo spec injection (no relevance filter) — rejected as token-bloaty.
- Bundling the now-nine `build_prompt` kwargs into a `ReviewContext` dataclass — threshold to reconsider, but not in this spec.
- Any change to `Agent`, `ReviewEngine.review` fan-out, `merger`, `MemoryStore`, `JSON_FORMAT_INSTRUCTIONS`, or `PASS_INSTRUCTIONS` — all untouched.
