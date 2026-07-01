# CLI Structured Logging & Git Diff Auto-Detect — Design

**Date:** 2026-07-01
**Status:** Approved
**Topic:** Add `--log-format json` to the CLI (unify with server logging) and make
`superseded review` auto-detect local git changes when no `--pr`/`--diff`/FILES is
given; add a `--staged` flag.

## Goal

Close the two smallest "Feature gaps for future work" items from `TODO.md`:

1. **Structured logging in CLI.** Only server mode emits JSON-formatted logs
   (`JsonFormatter` in `server/lifecycle.py`). The CLI review path relies on
   `logging.info` calls that are effectively dropped (no handler configured →
   Python's `lastResort` handler only surfaces `WARNING`+ with a bare format)
   and on `_status()` (`click.echo(..., err=True)`) for human progress. Add a
   `--log-format json` flag so both paths can emit structured logs identically.

2. **Native git diff auto-detect.** `superseded review` currently errors if
   neither `--pr`, `--diff`, nor positional FILES are supplied. Make the
   no-argument form useful by diffing the working tree against `HEAD`
   (everything uncommitted), and add `--staged` to scope the review to the
   index only.

## Scope & non-goals

**In scope:**

- Shared `JsonFormatter` + a `setup_logging()` helper, extracted out of the
  server module so the CLI can reuse it without importing server code.
- Group-level CLI options `--log-format` and `--log-level`, applied to every
  command (`review`, `init`, `feedback`, `serve`).
- New `Config` fields `log_format` and `log_level`.
- Env-var precedence `SUPERSEDED_LOG_FORMAT` / `SUPERSEDED_LOG_LEVEL`.
- `review` no-args path: `git diff HEAD`; `--staged` path: `git diff --cached`.
- Friendly handling of an empty auto-detected diff (no review run on nothing).

**Out of scope:**

- Diffing against the upstream tracking branch (`@{u}...HEAD`). The TODO
  explicitly specifies `git diff HEAD`; tracking-branch behavior is a separate
  decision.
- A `--quiet`/`--verbose` interaction model beyond `--log-level`.
- Changing `_status()` progress output (it is UI, not logging) or any output
  format (`--format`).
- Server-mode logging changes (it already uses `JsonFormatter`; it merely
  imports the class from its new home).

## Design

### Logging

**Shared module — `src/superseded/logging_utils.py`**

- `JsonFormatter(logging.Formatter)`: moved verbatim from
  `server/lifecycle.py` (same `_RESERVED_LOG_FIELDS` and `format()` body).
- `setup_logging(fmt: str, level: str) -> None`:
  - `fmt` is `"text"` (default) or `"json"`.
  - Resolves the integer level via `getattr(logging, level.upper(), logging.WARNING)`.
  - Configures the **root** logger: removes existing handlers, attaches a single
    `logging.StreamHandler(sys.stderr)` with:
    - `text` → `logging.Formatter("%(levelname)s %(name)s: %(message)s")`.
    - `json` → `JsonFormatter()`.
  - Sets `logging.getLogger("superseded").setLevel(level)` so library logs are
    gated even if third-party code raises the root level. Idempotent (safe to
    call once per command).

**Back-compat re-export**

- `server/lifecycle.py` keeps `from superseded.logging_utils import JsonFormatter`
  so existing `from superseded.server.lifecycle import JsonFormatter` imports
  (server `serve` command, any tests) keep working.

**Config**

- `Config` gains:
  - `log_format: str = "text"`
  - `log_level: str = "WARNING"`
- These round-trip through `write_config`/`load_config` like every other field.

**CLI options (group-level)**

- `--log-format {text,json}` (default from config), plus env
  `SUPERSEDED_LOG_FORMAT`.
- `--log-level` (free string, default from config), plus env
  `SUPERSEDED_LOG_LEVEL`.
- Resolution order mirrors `agent`/`model`: **env > flag > config**.
- Implemented as group-level options so a single `setup_logging()` call at the
  top of each command body (`review`, `init`, `feedback`, `serve`) applies them.
  Click passes group params via `ctx.obj`; each command reads them and calls
  `setup_logging()` before doing anything else.
- `serve` keeps its existing explicit `JsonFormatter` handler setup; calling
  `setup_logging()` first is harmless (it will be replaced by `serve`'s own
  `basicConfig` line). The `--log-format`/`--log-level` flags simply feed the
  defaults.

**Behavior preservation**

- Default `text` + `WARNING`: `INFO` logs remain silent (as today), `WARNING`+
  still surface to stderr, `_status()` progress is untouched. No change to
  stdout review output or `--format`.

### Git diff auto-detect

**`src/superseded/diff.py`**

- `fetch_diff(...)` signature gains `staged: bool = False`.
- New flow when `pr is None and diff_range is None and not files`:
  - `staged=True` → run `git diff --cached`.
  - `staged=False` → run `git diff HEAD`.
  - Implemented by a small internal helper `_fetch_raw_diff(args: list[str]) -> str`
    reused by both `_fetch_git_diff` and the auto-detect paths.
- Empty output from the auto-detect path raises
  `ValueError("no changes detected; stage/commit changes or pass --diff/--pr/FILES")`
  so the caller prints a clean message instead of running a review on nothing.
- Existing `--diff`/FILES/`--pr` paths are unchanged (the `staged` flag is only
  consulted on the no-args branch and is ignored otherwise).

**`src/superseded/cli.py`**

- `review` gains `--staged` (is_flag).
- The current hard error at the no-args guard (`pr is None and diff_range is
  None and not files`) is **removed**; the call proceeds and lets `fetch_diff`
  auto-detect. The existing `--post requires --pr` and `FILES + --pr` guards
  remain.
- `staged` is threaded through `_run_review` → `fetch_diff`.
- The empty-diff `ValueError` from `fetch_diff` is caught alongside the
  existing `RuntimeError` handler and rendered as `Error: <msg>` to stderr,
  exit 2.

**Semantics summary**

| Invocation                                  | Diff source                  |
|---------------------------------------------|------------------------------|
| `review` (no args)                          | `git diff HEAD`              |
| `review --staged`                           | `git diff --cached`          |
| `review --diff HEAD~3..HEAD`                | `git diff HEAD~3..HEAD`      |
| `review <FILES>`                            | `git diff HEAD -- <FILES>`   |
| `review --staged --diff X`                  | `git diff X` (`--staged` ignored; `--diff` wins) |
| `review --pr 123`                           | `gh pr diff`                 |

`--staged` only takes effect on the no-args branch; combining it with `--diff`
or FILES is allowed but has no effect (documented in `--help`).

## Testing

- `tests/test_logging.py` (new):
  - `setup_logging("text", "INFO")` attaches a stderr handler with the text
    format and routes a `logging.info` call to it (capfd).
  - `setup_logging("json", "INFO")` emits a single JSON line with
    `event`/`level`/`time` keys (capfd + `json.loads`).
  - Idempotency: calling twice does not double-add handlers.
  - `JsonFormatter` includes non-reserved `extra=` fields and serializes
    `exc_info`.
- `tests/test_diff.py`:
  - `fetch_diff(staged=True)` invokes `git diff --cached`.
  - `fetch_diff()` (no args) invokes `git diff HEAD`.
  - Both via mocked `subprocess.run`.
  - Empty stdout on the no-args path raises `ValueError`.
  - Existing `--diff`/FILES/`--pr` cases unchanged.
- `tests/test_cli.py`:
  - `--log-format json` group option calls `setup_logging` with `"json"`
    (monkeypatch `setup_logging` and assert call args).
  - `SUPERSEDED_LOG_FORMAT=json` env overrides the flag.
  - No-args `review` (mocked `fetch_diff`) does not exit 2; runs the review.
  - `review --staged` passes `staged=True` to `fetch_diff`.
  - Empty auto-diff surfaces the friendly error to stderr, exit 2.

## Risks & mitigations

- **Handler accumulation across command calls in tests:** `setup_logging` clears
  existing handlers first; verified by the idempotency test.
- **Breakage of server imports of `JsonFormatter`:** mitigated by the
  re-export in `server/lifecycle.py`; covered by existing server tests running
  unchanged.
- **Surprise from newly-shown logs:** default level stays `WARNING`, so
  behavior is identical unless the user opts into `--log-format json` or raises
  `--log-level`.

## Open questions

None.
