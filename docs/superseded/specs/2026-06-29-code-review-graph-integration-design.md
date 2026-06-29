# code-review-graph integration — Design

**Date:** 2026-06-29
**Status:** Approved (brainstormed)
**Scope:** Replace the `rg`-based caller lookup in `context/usage_retrieval.py` with a graph-aware equivalent backed by [code-review-graph](https://github.com/tirth8205/code-review-graph) when it is installed and a graph DB exists, while transparently falling back to the existing `rg` path otherwise.

## Goals

- Give every review pass structural context about who calls, is called by, and is tested by each changed symbol — instead of a flat ripgrep over identifier matches.
- Treat `code-review-graph` (CRG) as an **optional** enhancement: absent CRG, absent a built graph, or any runtime error in the CRG path must produce reviews identical to today's.
- Keep the change localized: no new context-dict keys, no change to prompts, no change to `Finding` shape, no change to the 5-pass orchestration.
- Mirror the existing `conventions` / `spec_retrieval` toggle pattern so users get the same env > flag > config precedence.

## Non-goals

- Replacing the `rg` call in `context/spec_retrieval.py` (text search inside markdown specs/plans/skills — CRG has nothing equivalent).
- Replacing `context/static_analysis.py` (`ruff`, `mypy`, `bandit`, `eslint`, etc.) — those are linter outputs, not graph queries.
- Bundling CRG as a hard dependency (would force a heavy Tree-sitter install on every user).
- Auto-installing CRG. Per the brainstorm, `superseded init` prints install instructions only.
- Surfacing CRG's blast-radius, flows, communities, or architecture-overview tools in this iteration. Out of scope; the first cut only mirrors today's per-symbol `### Usages of \`symbol\`` block.
- Talking to CRG over MCP. We use the in-process Python API.

## Integration model

CRG is an **optional Python dep** in a new `[project.optional-dependencies] graph` group. The decision of whether to use it is made at runtime per review, via three layered probes:

1. `import code_review_graph` succeeds.
2. `<root>/.code-review-graph/` exists and contains a built graph DB.
3. A single `code-review-graph update --brief` run (to refresh the graph before querying) exits successfully.

If any probe fails we log a `WARNING` and fall back to the existing `retrieve_usages` (rg) path. The review never fails because of CRG.

## Command / flag surface

No new top-level command. The new flag mirrors `--conventions` / `--no-conventions` and `--specs` / `--no-specs`:

```
superseded review --diff <range> [--graph | --no-graph]
```

- `--graph` / `--no-graph` — toggle graph-grounded usage retrieval (default: from config, ultimately `true`).
- `SUPERSEDED_GRAPH` env var — overrides flag and config; intended for GitHub Action secrets, mirroring `SUPERSEDED_AGENT` / `SUPERSEDED_MODEL`.
- `.superseded.yaml` gains a top-level `graph: true|false` key.

Precedence, identical to `resolve_agent` / `resolve_model` in `cli.py`: **env > CLI flag > config file > model default (`true`)**.

## `superseded init` extension

`detection.py` gains one new pure function:

```python
def detect_code_review_graph(root: Path) -> bool:
    """True iff `import code_review_graph` succeeds AND
    (root / ".code-review-graph") exists."""
```

`cli.py`'s `_run_init` calls it after the existing `gh` check. When it returns `False`, `init` prints exactly one status line to stderr (no failure, no exit code change):

```
code-review-graph: not installed (graph-grounded reviews disabled; install with: uv add code-review-graph && code-review-graph build)
```

When it returns `True`:

```
code-review-graph: found
```

No auto-install. No write to the config file beyond the existing fields (the new `graph: true` default in `Config` covers it).

## Architecture

### New: `src/superseded/context/graph_retrieval.py`

```python
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from superseded.context.usage_retrieval import USAGE_BUDGET, extract_symbols
from superseded.diff import parse_diff_files

logger = logging.getLogger(__name__)

_GRAPH_DIR = ".code-review-graph"
_REFRESH_TIMEOUT = 30  # seconds; CRG advertises <2s incremental, this is the safety bound


def is_available(root: Path) -> bool:
    """True iff code_review_graph imports AND a built graph exists at <root>/.code-review-graph."""
    try:
        import code_review_graph  # noqa: F401
    except ImportError:
        return False
    return (root / _GRAPH_DIR).is_dir()


def ensure_graph_fresh(root: Path) -> None:
    """Best-effort incremental graph refresh. Never raises."""
    try:
        subprocess.run(
            ["code-review-graph", "update", "--brief"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=_REFRESH_TIMEOUT,
        )
    except FileNotFoundError:
        logger.warning("code-review-graph CLI not on PATH; graph will be used as-is")
    except subprocess.TimeoutExpired:
        logger.warning("code-review-graph update timed out after %ds; using stale graph", _REFRESH_TIMEOUT)
    except OSError as err:
        logger.warning("code-review-graph update failed: %s", err)


def _query_callers_and_tests(symbol: str, root: Path) -> list[str]:
    """Return caller/test lines for `symbol` from the graph.

    Implementation detail resolved during implementation:
    prefer code_review_graph's in-process API (e.g. CodeReviewGraph.query /
    query_graph_tool) for callers + tests edges of the matched node(s).
    If only the CLI is usable, fall back to:
        code-review-graph query --callers <symbol>
        code-review-graph query --tests <symbol>
    Output is normalized to `path:line: snippet` lines to match the rg output shape.
    """


def retrieve_usages_via_graph(diff: str, root: Path) -> str | None:
    """Graph-grounded drop-in replacement for usage_retrieval.retrieve_usages.

    Reuses extract_symbols() so the symbol set is identical to the rg path.
    Produces `### Usages of \`symbol\`` blocks under the same USAGE_BUDGET.
    Returns None when no symbols or no caller data found.
    """
    entries = parse_diff_files(diff)
    if entries:
        symbols: list[str] = []
        seen: set[str] = set()
        for entry in entries:
            lang = _LANG_MAP.get(Path(entry["file"]).suffix)
            for sym in extract_symbols(entry["diff"], lang):
                if sym not in seen:
                    seen.add(sym)
                    symbols.append(sym)
        symbols = symbols[-MAX_SYMBOLS:]
    else:
        symbols = extract_symbols(diff, None)

    if not symbols:
        return None

    blocks: list[str] = []
    total_chars = 0
    for sym in symbols:
        lines = _query_callers_and_tests(sym, root)
        if not lines:
            continue
        block = f"### Usages of `{sym}`\n" + "\n".join(lines)
        if total_chars + len(block) > USAGE_BUDGET:
            omitted = len(symbols) - len(blocks)
            blocks.append(f"\u2026 ({omitted} more usages omitted by retrieval budget)")
            break
        blocks.append(block)
        total_chars += len(block)

    if not blocks:
        return None
    return "\n\n".join(blocks)
```

Notes:

- `extract_symbols`, `USAGE_BUDGET`, `MAX_SYMBOLS`, and `_LANG_MAP` are imported from `usage_retrieval` so the two paths agree on what counts as a "changed symbol" and on truncation policy. Those names become **public** parts of `usage_retrieval`'s API (they already exist there; this just formalizes them).
- `_query_callers_and_tests` is the single CRG-touching point. It returns the same `path:line: snippet` shape as `rg -n`, so the block formatting above is byte-for-byte compatible with today's `retrieve_usages` output and downstream prompts need no change.
- All CRG exceptions (whatever the in-process API raises) are caught here and converted to an empty list — that degrades gracefully to "no usages found for this symbol" rather than aborting the whole review.

### Extended: `src/superseded/context/gathering.py`

Add a `graph: bool = False` parameter to `gather_context`. When `True`:

1. Submit `ensure_graph_fresh(root)` on the executor and block on its result before submitting any retrieval (the refresh must complete before queries read the graph). This is a single ~2s call; it serializes with itself but still parallelizes with `file_context`, `static_signals`, etc. that were submitted before it.
2. If `graph_retrieval.is_available(root)` is `True`, submit `retrieve_usages_via_graph` as the `usage_signals` future. Otherwise submit the existing `retrieve_usages` (rg).
3. The returned dict keeps the `usage_signals` key — no downstream change.

When `graph=False`, behavior is unchanged.

### Extended: `src/superseded/config.py`

```python
class Config(BaseModel):
    ...
    spec_retrieval: bool = True
    graph: bool = True  # NEW
```

One new field, default `True`. `write_config` round-trips it automatically via `model_dump`.

### Extended: `src/superseded/cli.py`

- New click option on `review`: `--graph/--no-graph` (default `None`, meaning "defer to config/env").
- New `resolve_graph(cli_value: bool | None, config: Config) -> bool` helper mirroring `resolve_agent` / `resolve_model`:

  ```python
  def resolve_graph(cli_value: bool | None, config: Config) -> bool:
      env = os.environ.get("SUPERSEDED_GRAPH")
      if env is not None:
          return env.lower() in ("1", "true", "yes", "on")
      if cli_value is not None:
          return cli_value
      return config.graph
  ```

- Passed through to `gather_context(..., graph=resolved)`.
- The `serve` command's pass-construction path also threads `graph=resolved` through, since it ultimately calls `gather_context` per review.

### Extended: `src/superseded/detection.py`

Add `detect_code_review_graph(root)` as shown above. Imported by `cli.py`'s `_run_init`. No change to `detect_agents` or `detect_gh`.

### Extended: `pyproject.toml`

```toml
[project.optional-dependencies]
graph = ["code-review-graph"]
```

Users opt in via `uv sync --extra graph` or `uv add code-review-graph`. The base install is unchanged.

## Error model

Every CRG touchpoint is best-effort:

| Failure                              | Where caught                                | Behavior                                            |
|--------------------------------------|---------------------------------------------|-----------------------------------------------------|
| CRG not importable                   | `is_available`                              | `False`; gathering falls back to rg path            |
| `.code-review-graph/` missing        | `is_available`                              | `False`; gathering falls back to rg path            |
| `code-review-graph update` not found | `ensure_graph_fresh`                        | `WARNING` log; queries run against the stale graph  |
| `update` times out                   | `ensure_graph_fresh`                        | `WARNING` log; queries run against the stale graph  |
| `update` exits non-zero / raises     | `ensure_graph_fresh`                        | `WARNING` log; queries run against the stale graph  |
| Query raises                         | `_query_callers_and_tests`                  | Empty list for that symbol; other symbols continue  |
| All symbols yield empty caller lists | `retrieve_usages_via_graph`                 | Returns `None`; `usage_signals` block is omitted    |

The review never aborts because of CRG. The worst observable degradation is "no usage_signals block in the prompt", identical to today's behavior when `rg` finds no callers.

## Testing

All CRG-touching tests use mocks. No real graph build in CI.

### New: `tests/test_graph_retrieval.py`

- `test_is_available_false_when_import_missing` — monkeypatch `builtins.__import__` to raise `ImportError` on `code_review_graph`; assert `is_available` returns `False`.
- `test_is_available_false_when_dir_missing` — temp dir without `.code-review-graph`; assert `False`.
- `test_is_available_true` — temp dir with `.code-review-graph/` subdir and importable stub module; assert `True`.
- `test_ensure_graph_fresh_swallows_file_not_found` — monkeypatch `subprocess.run` to raise `FileNotFoundError`; assert no propagation.
- `test_ensure_graph_fresh_swallows_timeout` — raise `TimeoutExpired`; assert no propagation.
- `test_ensure_graph_fresh_swallows_oserror` — raise `OSError`; assert no propagation.
- `test_ensure_graph_fresh_passes_cwd` — capture the subprocess.run call kwargs; assert `cwd=root`.
- `test_retrieve_usages_via_graph_no_symbols` — empty diff; assert `None`.
- `test_retrieve_usages_via_graph_formats_blocks` — monkeypatch `_query_callers_and_tests` to return canned `["a.py:1: foo", "b.py:2: bar"]` for one symbol; assert the `### Usages of \`sym\`` block shape matches `retrieve_usages` output.
- `test_retrieve_usages_via_graph_budget_truncation` — stub returns long lines for many symbols; assert the truncation tail and budget cap match `USAGE_BUDGET`.
- `test_retrieve_usages_via_graph_returns_none_when_no_callers` — stub returns `[]` for every symbol; assert `None`.
- `test_retrieve_usages_via_graph_query_exception_degrades` — stub raises; assert that symbol yields no block but others still appear.

### New: `tests/test_gathering_graph.py`

- `test_gather_context_graph_false_uses_rg` — `graph=False`; assert `usage_signals` came from `retrieve_usages` (spy).
- `test_gather_context_graph_true_available_uses_graph` — `graph=True`; monkeypatch `graph_retrieval.is_available` → `True`; assert `usage_signals` came from `retrieve_usages_via_graph` and that `ensure_graph_fresh` was called first.
- `test_gather_context_graph_true_unavailable_falls_back` — `graph=True`; monkeypatch `is_available` → `False`; assert rg path used, `ensure_graph_fresh` not called.

### Extended: `tests/test_config.py`

- `test_config_graph_default_true` — `Config().graph is True`.
- `test_config_graph_round_trip` — write/load preserves `graph: false`.

### Extended: `tests/test_cli.py` (or equivalent)

- `test_resolve_graph_env_overrides_flag` — set `SUPERSEDED_GRAPH=false`, pass `--graph`; assert resolved `False`.
- `test_resolve_graph_flag_overrides_config` — config `graph: false`, pass `--graph`; assert resolved `True`.
- `test_resolve_graph_defaults_to_config` — no env, no flag; assert matches config.
- `test_resolve_graph_defaults_true` — no env, no flag, default config; assert `True`.
- `test_review_passes_graph_to_gather_context` — invoke `review` via `CliRunner`; spy on `gather_context`; assert the `graph` kwarg matches the resolved value.

### Extended: `tests/test_init.py`

- `test_init_crg_missing_prints_instruction` — monkeypatch `detect_code_review_graph` → `False`; assert the install-instruction line is in stderr, exit code `0`, file written.
- `test_init_crg_present_prints_found` — monkeypatch → `True`; assert "found" line, exit `0`.

## Documentation updates

- `AGENTS.md` "Architecture notes" gains a sentence noting that `context/graph_retrieval.py` optionally grounds usage retrieval in CRG and falls back to `rg`.
- `AGENTS.md` "Conventions" section notes the new `graph` config field and `--graph`/`--no-graph` flag, alongside the existing `conventions` / `spec_retrieval` entries.
- `AGENTS.md` "Gitignore gotchas" notes `.code-review-graph/` is already gitignored.
- `README` (if it lists context features) gains a one-line mention + the install snippet.

## Risk / trade-offs

- **CRG API surface is verified at implementation time.** The README lists `query_graph_tool` (callers / callees / tests / imports / inheritance) as an MCP tool; the in-process Python entry point is documented but the exact import path (`code_review_graph.graph.CodeReviewGraph` vs. a higher-level façade) will be confirmed by reading the installed package during implementation. If only the CLI is usable, `_query_callers_and_tests` shells out to `code-review-graph query` and parses stdout — the contract (return `path:line: snippet` lines) is unchanged either way.
- **`ensure_graph_fresh` serializes one ~2s refresh into every review.** Accepted: CRG advertises sub-2s incremental updates on 2,900-file repos; this is a one-time cost paid once per `review` invocation, parallelizable with the non-CRG context futures. Users who want to skip it can pass `--no-graph`.
- **Symbol-to-node matching is fuzzy.** CRG nodes are functions/classes keyed by name + file; `extract_symbols` only knows names. The query will match all nodes with that name across the graph, which may include unrelated definitions in other files. Mitigation: in implementation we filter CRG results to nodes whose file is *not* in the changed-files list (callers, not the change itself), matching today's rg behavior (which also excludes changed files via `--glob`).
- **No new context-dict key.** Reusing `usage_signals` means consumers (prompts, JSON output) don't change. Trade-off: when CRG and rg disagree on coverage, downstream sees only the winner's output — there's no side-by-side. Accepted: the goal is to replace the rg path, not augment it.
- **Optional dep means CI tests must mock both paths.** The matrix above covers both `is_available=True` (graph path) and `is_available=False` (rg fallback) without requiring CRG in the test environment.
