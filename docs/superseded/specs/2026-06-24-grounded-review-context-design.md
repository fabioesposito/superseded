# Grounded Review Context — Design

Date: 2026-06-24
Status: Draft (pending user review)
Scope: Two of three grounded-review features extracted from the CodeRabbit architecture article — the "pipeline" half of the hybrid-AI pattern. This spec covers (1) a static-analysis pre-pass that runs detected linters/scanners over changed files and (2) a cross-file usage retrieval step that surfaces callers of changed symbols, both injected into the per-pass prompt as curated context.

Companion spec: `2026-06-24-reasoning-trail-design.md` (output side — feature #3).

## Motivation

Superseded currently feeds each AI pass only `diff` + a `file_context` window of ±20 lines around each hunk + an optional `memory_context` of dismissed human feedback (see `prompts.py:47`, `diff.py:7`). The agent is asked to review a change with:

- No deterministic signal from linters/scanners the project already uses (ruff, mypy, eslint, bandit, gitleaks, go vet, …). The agent re-discovers what static tooling already knows, slowly.
- No visibility into how changed symbols are *used* outside the ±20-line window. A change can break a caller four directories away that a human misses on a busy day.

CodeRabbit's headline architectural lesson is *hybrid AI = a deterministic pipeline feeding curated context to an agent*, contrasted with a free-roaming agent that wanders and burns context. The article calls this out twice: "Favor hybrid AI: a repeatable pipeline with a small agent for targeted reasoning," and "Context is curated — not left for the model to wander."

This spec ports that lesson locally. Both features produce deterministic, repository-grounded text blocks that the agent can reason over without leaving its sandbox-supervised CLI shell.

## Design choices (decided)

| Decision | Choice | Rejected alternative | Rationale |
|---|---|---|---|
| Tool config philosophy | Auto-detect from repo | Explicit config only; hybrid auto+override | Zero config; works on first run. Ops made the article's point that CodeRabbit curates by default. |
| v1 tool coverage | Python + JS/TS + Go | Python-only fixed; Python+JS split | Matches the languages asked for without a fixed-list refactor later (pluggable `Tool` protocol instead). |
| Run scope | Changed files only | Whole repo; changed + import targets | Fast, diff-relevant; avoids minutes-long whole-repo runs that would dominate the 300 s agent timeout. |
| Retrieval method | `rg` over symbols | Tree-sitter go-to-callers; hybrid rg+ts | Language-agnostic, no grammar deps, near-instant. Precision is recovered at the agent layer (it filters fuzzy hits). |
| Context budget | Per-block char caps | Shared token budget across all context; no caps | Predictable, no tokenizer dep. Each block keeps its high-signal head; the tail is dropped with a count. |
| On-by-default | Yes, opt-out via config | Off by default, opt-in | 'Curated by default' — works on first run; `static_analysis: false` in `.superseded.yaml` disables. |
| Failure mode | Skip silently + warn in log | Surface gap in prompt | Matches existing per-pass failure handling (`engine.py:106`); never fatal; no prompt-token noise. |
| Architecture shape | Pure modules + loose new kwargs | `ReviewContext` dataclass; engine-owns-context | Matches `diff.py`'s existing pattern; keeps `engine.review` a thin pass fan-out; smallest blast radius. |

## Architecture

### Where enrichment lives

New package `src/superseded/context/` houses two pure-function modules. `cli.py` — which already calls `compute_file_context()` from `diff.py` — calls the new functions in the same place and threads the resulting strings through `engine.review()` → `build_prompt()` as two new optional kwargs.

```
cli.py
  ├─ fetch_diff()                            [existing]
  ├─ parse_diff_files(diff)                  [existing, reused] → changed_files
  ├─ compute_file_context(diff)              [existing]
  ├─ static_signals  = run_static_analysis(changed_files, root)   [new]
  ├─ usage_signals   = retrieve_usages(diff, root)                [new]
  └─ engine.review(..., static_signals=, usage_signals=)
                                                         │
                                                         ▼
build_prompt(pass_name, diff, ..., static_signals, usage_signals)
   inserts two new ### sections into the prompt template
```

Nothing else changes: `Agent`, `ReviewEngine.review` (still a pass fan-out via `ThreadPoolExecutor`), `merger`, `MemoryStore` are untouched.

### Why no `ReviewContext` dataclass

Considered bundling the four loose kwargs (`file_context`, `memory_context`, and the two new ones) into a `ReviewContext` object built by a `ContextBuilder`. Rejected because (a) the payoff only materializes if many more blocks are expected — but feature #3 lives on the output side and won't add blocks here; (b) it would be a signature-breaking refactor of `engine.review`/`build_prompt` for no current payoff. The loose-kwargs style matches `diff.py` already.

### Why the engine doesn't own context

Considered having `ReviewEngine` call the context modules itself before fanning out. Rejected because it would couple the engine to context tooling, making it harder to unit-test the pass fan-out in isolation, and would push us toward a god-object as more enrichment is added.

## Module 1 — `context/static_analysis.py`

### `Tool` protocol

```python
class Tool(Protocol):
    name: str
    languages: list[str]        # ["python", "js", "ts", "go", "*"] — "*" = language-agnostic
    def detect(self, root: Path) -> bool: ...
    def build_command(self, changed_files: list[str], root: Path) -> list[str]: ...
    def parse_output(self, stdout: str, stderr: str, root: Path) -> str: ...   # returns the block text
```

`parse_output` receives both stdout and stderr because some tools report findings via stderr only (`tsc` writes diagnostics to stderr; `gofmt -l` lists files on stdout, nothing on stderr).

A module-level `TOOLS: list[Tool] = [...]` registry. Adding a tool = implement the protocol, append to `TOOLS`. No decorators, no base class — keeps it boring and aligned with the existing `AGENT_MAP` flat-dict style in `engine.py:21`.

### `run_static_analysis(changed_files, root) -> str | None`

1. Partition `changed_files` by language using file extension (`.py`, `.js/.jsx/.mjs/.cjs`, `.ts/.tsx`, `.go`).
2. Iterate `TOOLS`; for each, if its `languages` intersect the languages present in `changed_files` and `detect()` returns True, build the command and `subprocess.run` it.
3. Concatenate `parse_output()` results from surviving tools into one block string. Return `None` if no tool produced output.
4. Truncate the aggregate block to `STATIC_BUDGET` chars (see Budget).

### Initial tool set

| Tool | Languages | detect | command | output channel |
|---|---|---|---|---|
| `RuffTool` | python | `pyproject.toml` contains `ruff` dep **or** `[tool.ruff]` section | `ruff check --output-format=concise <py files>` | stdout |
| `MypyTool` | python | `pyproject.toml` contains `mypy` dep **or** `[tool.mypy]` section | `mypy --no-error-summary <py files>` | stdout |
| `BanditTool` | python | `pyproject.toml` contains `bandit` dep | `bandit -q <py files>` | stdout |
| `EslintTool` | js/ts | `.eslintrc*`, `eslint.config.*`, or `eslintConfig` key in `package.json` | `eslint --format=compact <js/ts files>` | stdout |
| `TscTool` | ts | `tsconfig.json` exists | `tsc --noEmit` (no file args — reads `tsconfig.json`) | stderr |
| `GofmtTool` | go | `go.mod` exists | `gofmt -l <go files>` | stdout (lists files needing formatting) |
| `GoVetTool` | go | `go.mod` exists | `go vet <go packages>` (use `.` for the whole module if changed files span packages) | stdout/stderr |
| `StaticcheckTool` | go | `go.mod` exists **and** `staticcheck` on PATH | `staticcheck <go files>` | stdout |
| `GitleaksTool` | `*` (secrets) | `.git` exists (always, unless disabled) | `gitleaks dir scan --source . --no-banner --report-format json` (still filter to changed files in `parse_output`) | stdout (JSON) |

`gitleaks` is the one whole-repoexception — it cannot scan a file list meaningfully and secrets detection is inherently repo-wide. Its `parse_output` filters the JSON report down to findings whose `RuleID`/`Secret` line falls within a changed file's range. If `gitleaks dir` is too slow in practice (>5 s empirically), it stays opt-in via a follow-up; v1 ships it gated on detect.

### Non-zero exits are load-bearing

Linters report findings via non-zero exit codes (eslint=1 on any violation, ruff=1, bandit=1). Unlike `engine.py:55`, **non-zero exit does not abort** the tool — we still parse stdout/stderr. Only `FileNotFoundError` (binary not on PATH) and `TimeoutExpired` cause the tool to be skipped. The existing engine precedent of skipping on error is honored via `logger.warning` then omitting the tool's block from the aggregate.

### Timeouts

`subprocess.run(..., timeout=30)` for each static tool. `30 s` is generous for changed-files-only scope but bounds a wedged linter against the 300 s agent budget. A timeout results in `logger.warning("Static tool %s timed out after 30s, skipping", name)` and the tool's output is omitted.

### Missing-binary handling

`FileNotFoundError` from `subprocess.run` (binary not on PATH) → `logger.warning("Static tool %s not on PATH, skipping", name)`, omit block. This is the common case on CI runners without all tools installed and must never be fatal.

## Module 2 — `context/usage_retrieval.py`

### Symbol extraction (per-language)

Per-language `SymbolExtractor` reads **added** diff lines (lines starting with `+` in hunks). Regexes:

| Language | Patterns |
|---|---|
| Python | `^\s*def\s+(\w+)`, `^\s*async\s+def\s+(\w+)`, `^\s*class\s+(\w+)`, `^\s*([A-Z]\w*)\s*=` (constants/types), `^\s*(\w+)\s*:\s*` (annotated module vars) |
| JS/TS | `^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)`, `^\s*(?:export\s+)?class\s+(\w+)`, `^\s*(?:export\s+)?const\s+(\w+)\s*=`, `^\s*(?:export\s+)?interface\s+(\w+)`, `^\s*(?:export\s+)?type\s+(\w+)` |
| Go | `^\s*func(?:\s+\([^)]+\))?\s+(\w+)`, `^\s*type\s+(\w+)\s+struct`, `^\s*type\s+(\w+)\s+interface`, `^\s*var\s+(\w+)`, `^\s*const\s+(\w+)` |
| Fallback | any `\b[A-Za-z_]\w{3,}\b` identifier on added lines (recall booster for symbols missed by per-language patterns) |

### Normalization

- Dedupe (case-sensitive for Go, case-insensitive for Python/JS/TS to catch `MyClass`/`myClass`).
- Drop a small keyword/blocklist (`self`, `cls`, `return`, `if`, `else`, `for`, `while`, `import`, `from`, `const` in JS contexts, `func`, `type`, `var`, `package`, `func` in Go contexts, plus Python keywords via `keyword.kwlist`).
- Cap at **25 symbols**, most-recently-added first (later-diff additions tend to be the focal change; beyond 25 blows the retrieval budget).

### `rg` invocation

For each symbol in order, run:
```
rg -n --max-count 4 '\b<sym>\b' <root> \
   --glob '!<changed file>' --glob '!*.lock' --glob '!.venv/**' --glob '!node_modules/**' --glob '!.git/**'
```
`--max-count 4` caps per-file matches per symbol (matches CodeRabbit's "curated" philosophy — we don't dump every callsite; the agent reasons over representative ones). Excludes files in the diff (we want *callers*, not the changed file's own internal use).

Concatenate surviving matches up to `USAGE_BUDGET` chars. Stop early once the cap is hit.

### `rg` missing handling

If `rg`/`ripgrep` is not on PATH, `logger.warning("ripgrep not on PATH, skipping usage retrieval")` and return `None`. Never fatal — the static-analysis block can still ship without the retrieval block.

## Prompt changes — `prompts.py:build_prompt`

Two new optional kwargs `static_signals: str | None`, `usage_signals: str | None`. The template gains two new `### …` sections positioned **between** `### Changed Files (diff)` and `### File Context` — signals explain the diff; file context is local detail. Each section renders only when its kwarg is non-None and non-empty, using the existing `ctx or "No … available."` idiom (`prompts.py:56`):

```
### Static analysis signals (run before AI; deterministic)
{static_signals or "No static analysis tools detected or available."}

### Cross-file usages (callers of changed symbols, ±3 lines)
{usage_signals or "No usages retrieved."}
```

`JSON_FORMAT_INSTRUCTIONS` (`prompts.py:26`), the role block (`prompts.py:59-63`), and the rules block (`prompts.py:64-69`) are **untouched** — the agent's job description doesn't change, only its grounding does. Agents that fail to parse the new sections degrade gracefully to the old prompt shape since the sections are additive.

## Context budget — per-block char caps

Module-level constants, enforced *at concat time inside each module* (not in the prompt — keeps the modules honest when reused elsewhere):

| Block | Budget | Truncation tail |
|---|---|---|
| `static_analysis` aggregate | 4000 chars | `… ({N} more findings omitted by static-analysis budget)` |
| `usage_retrieval` aggregate | 6000 chars | `… ({N} more usages omitted by retrieval budget)` |

`usage_retrieval` gets a larger budget because each match is bigger (filename + n lines × up to 25 symbols). The existing `file_context` (±20 lines from `diff.py:7`) keeps its current implicit cap (whatever the diff contains). No shared token budget, no tokenizer dep.

## Config — `config.py`

`Config` gains two plain bools:
```python
static_analysis: bool = True
usage_retrieval: bool = True
```
`.superseded.yaml` can disable:
```yaml
static_analysis: false
usage_retrieval: false
```
No CLI flags in v1 (YAGNI — config is enough; can be added later if needed). The `is_pass_enabled` pattern (`config.py:25`) is intentionally *not* reused — the new fields aren't subset multi-selects, they're plain on/off toggles and are checked directly in `cli.py` with `if config.static_analysis:`.

## Repo root resolution

`cli.py` already calls `current_repo()` (`output/github_pr.py`) for memory and gets back an `owner/repo` string. We need a *path* for the new modules. Add a tiny helper `repo_root() -> Path` running `git rev-parse --show-toplevel` and falling back to `Path.cwd()` on any subprocess error. Both new modules receive it as `root`. The helper lives in `diff.py` next to the other `subprocess`-wrapping functions (`fetch_diff`, `_fetch_git_diff`) rather than in `context/` to avoid a cross-import cycle if context ever needs to reuse it.

## Failure handling summary

| Fault | Module | Behavior |
|---|---|---|
| Tool binary not on PATH | static_analysis | `logger.warning`, omit that tool's block, run other tools, never fatal |
| Tool times out (30 s) | static_analysis | `logger.warning`, omit block, run other tools, never fatal |
| Tool exits non-zero | static_analysis | **Parse stdout/stderr anyway** — linters communicate violations via non-zero exit |
| `rg` not on PATH | usage_retrieval | `logger.warning`, return `None`, never fatal |
| No tools detected | static_analysis | Return `None` → prompt section renders the "No … available." placeholder |
| No symbols in diff | usage_retrieval | Return `None` → prompt section renders the "No usages retrieved." placeholder |
| `git rev-parse` fails | diff.py:repo_root | Fall back to `Path.cwd()`, continue |

## Testing plan

### New: `tests/test_context_static.py`

- **`detect()` truth-tables** per tool: fixture repos (one per language) with appropriate lockfiles/configs assert `detect()` returns True; repos missing those configs return False. Parametrized.
- **`build_command()` shape** per tool: assert the exact argv constructed for a given changed-files list. Parametrized.
- **`parse_output()` truncation + tail**: feed canned stdout exceeding `STATIC_BUDGET`, assert truncation and that the tail includes the omitted count.
- **`FileNotFoundError` path**: monkeypatch `subprocess.run` to raise `FileNotFoundError`; assert the tool's block is omitted, a warning is logged (use `caplog`), and `run_static_analysis` still returns output from other tools.
- **`TimeoutExpired` path**: monkeypatch to raise `TimeoutExpired`; assert skip + warn + non-fatal.
- **Non-zero exit path**: monkeypatch to return `CompletedProcess(returncode=1, stdout=<violations>, stderr="")`; assert `parse_output` is still called and the violations surface in the block.
- **End-to-end `run_static_analysis` over a fixture repo**: monkeypatch `subprocess.run` to canned outputs; assert aggregate block ordering (alphabetical by `name`), truncation tail, and `None` when nothing detected.

### New: `tests/test_context_usage.py`

- **Symbol extraction** per-language: golden-input diff strings → expected symbol lists including dedupe, keyword filter, and the 25-symbol cap. Parametrized.
- **`rg` invocation mocked**: monkeypatch `subprocess.run` to return canned `rg -n` output; assert the block contains the expected matches, correct `--glob` exclusion flags, and `--max-count 4`.
- **`rg` missing**: monkeypatch to raise `FileNotFoundError`; assert `retrieve_usages` returns `None` and warns.
- **Truncation tail**: feed enough canned matches to exceed `USAGE_BUDGET`; assert tail wording and count.
- **Diff with no added lines / no extractable symbols**: assert returns `None`.

### New: `tests/test_prompts.py`

- Sections present when kwargs non-empty; absent / replaced with placeholder when `None`.
- Section ordering: `### Changed Files (diff)` → `### Static analysis signals` → `### Cross-file usages` → `### File Context` → `### Past Feedback`.
- Existing sections (role, rules, JSON format) unchanged when new kwargs are `None` (regression: full old prompt string still equal).

### Extended: `tests/test_integration.py`

- End-to-end with both enrichment functions monkeypatched to canned strings; assert the canned strings land in the prompt the agent CLI receives (assert on the `prompt` argument passed to `agent.build_command` via the existing mock pattern in `test_integration.py`).
- `static_analysis=False` / `usage_retrieval=False` in config → assert the corresponding functions are not called and the prompt contains the placeholder sections.

## Out of scope (explicit)

- Tree-sitter / AST retrieval, LSP, multi-hop import walks.
- Whole-repo scans (except `gitleaks`'s one carve-out); only changed files.
- Shared token-budget / tokenizer dep.
- New CLI flags (config-only in v1).
- Any change to `Agent`, `ReviewEngine.review`, `merger`, `MemoryStore` — all untouched in this spec.
- Reasoning-trail persistence and merger-by-rationale — covered by the companion spec `2026-06-24-reasoning-trail-design.md`.