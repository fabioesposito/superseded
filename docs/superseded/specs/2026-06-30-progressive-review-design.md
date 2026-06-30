# Progressive PR Review — Design

**Date:** 2026-06-30
**Status:** Approved
**Topic:** Incremental ("progressive") PR review keyed on a per-PR commit watermark.

## Goal

Remember the last-reviewed commit for each PR so subsequent reviews only cover
the new commits. Reduces cost and noise on long-lived PRs that receive many
pushes.

## Scope & non-goals

**In scope:**

- Both review entry points:
  - CLI `superseded review --pr <n>` (watermark in local
    `.superseded/memory.db`).
  - Server-mode worker / GitHub App (watermark in the server's own store).
- Each entry point maintains its own independent watermark — no cross-sync.
- `--pr` only. `--diff` and file-only reviews are unchanged.

**Out of scope:**

- Progressive review for `--diff` / local branch runs.
- Cross-entry-point watermark sync (CLI and server keep separate state).
- A dedicated `reset` / `forget` subcommand (`--full` and self-healing fallback
  cover the same need; a command can be added later).

## Behavior summary

- **On by default** when memory is enabled. A `--full` flag (CLI) and
  `config.progressive: false` (CLI + server) force a full review.
- Watermark is the PR's `head_sha` from the last successful review, keyed by
  `(repo, pr_number)`.
- Incremental diff is fetched via the **GitHub compare API**
  (`gh api repos/{o}/{r}/compare/{base}...{head}`), which returns both the
  ancestry `status` and (on re-request with the diff `Accept` header) the patch.
- **Stale watermark** (rebase / force-push / squash → `status` of `behind` or
  `diverged`, or compare API error) → fall back to full review with a warning.
  The watermark is then advanced to the new head, so a rebase self-heals.
- **No new commits** (`status == identical`) → CLI prints "no new commits since
  `<sha>`" and an empty result, exit 0; server completes the check run as
  `success` with title "No new commits since last review" and posts nothing.
- **Watermark is written only after a successful review.** Failed/timed-out
  passes do not advance the watermark; the next run retries from the same base.
- **`--no-memory` / `config.memory == false`**: no store → no watermark → always
  full review. CLI emits a one-line note explaining progressive needs memory.

## Data model

New table added to the shared `MemoryStore` schema (CLI and server use the same
class; separate DB files keep their state independent):

```sql
CREATE TABLE IF NOT EXISTS review_watermarks (
    repo        TEXT    NOT NULL,
    pr_number   INTEGER NOT NULL,
    head_sha    TEXT    NOT NULL,
    reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (repo, pr_number)
);
```

`repo` is the `owner/name` slug already produced by `current_repo()` and used by
the `findings` table. `(repo, pr_number)` is the natural key — one watermark per
open PR.

New `MemoryStore` methods:

- `async def get_watermark(repo: str, pr_number: int) -> str | None`
- `async def set_watermark(repo: str, pr_number: int, head_sha: str) -> None`
  (INSERT OR REPLACE).

The `CREATE TABLE` is added to `SCHEMA` (fresh DBs) and to `_migrate()`
(idempotent `CREATE TABLE IF NOT EXISTS` for existing DBs).

## Incremental diff layer

New module `src/superseded/incremental.py` (keeps `diff.py` focused on existing
fetch/parse). Single function:

```python
def fetch_incremental_diff(
    owner: str, repo: str, base_sha: str, head_sha: str
) -> tuple[str | None, str]:
    """Return (diff, status) via the GitHub compare endpoint.

    status ∈ {"ahead", "identical", "diverged"}.
    diff is the patch string for status == "ahead"; None otherwise.
    Raises IncrementalDiffError on gh/API failure (caller falls back to full).
    """
```

Implementation shells out to `gh api`:

1. `gh api repos/{owner}/{repo}/compare/{base}...{head}` (JSON) → read `.status`
   ∈ `ahead | behind | identical | diverged`.
2. If `status == "ahead"`: re-request with header
   `Accept: application/vnd.github.v3.diff` → patch body.

`behind` is normalized to `diverged` for the caller (both mean "base is not an
ancestor of head" from the progressive-review perspective).

Companion helper in `diff.py` (needed by CLI; server already has `head_sha` on
the job):

```python
def fetch_pr_head_sha(pr: int) -> str:
    # gh pr view <pr> --json headRefOid -q .headRefOid
```

### Server-side equivalent

`GitHubApp` gains an `aiohttp` method alongside `fetch_pr_diff`:

```python
async def compare_diff(
    self, token: str, owner: str, repo: str, base: str, head: str
) -> tuple[str | None, str]:
    """Return (patch_or_none, status). Same contract as the CLI helper."""
```

Calls the same REST compare endpoint with the installation token; reads
`.status` from JSON, re-requests with the diff `Accept` header when `ahead`.

### status → action map

| `status` | Meaning | Action |
|---|---|---|
| `identical` | head == base, no new commits | CLI: empty result, exit 0. Server: check run `success`, "No new commits" title, no comments/findings. Watermark unchanged. |
| `ahead` | base is an ancestor of head | Use returned patch as the diff. Review it. Advance watermark to `head_sha`. |
| `diverged` (incl. `behind`) | base not an ancestor (rebase/force-push/squash) | Fall back to full `gh pr diff <pr>` with a stderr warning. Advance watermark to `head_sha` after success. |

Any exception from `gh api` / the compare endpoint is caught and **treated as
fall-back to full review** with a warning. Progressive is a perf optimization
and never blocks a review.

## CLI wiring

`review` gains `--full` (default `False`). Progressive logic activates only for
`--pr` and only when `config.memory and not no_memory and config.progressive`.

`_run_review` flow for `--pr` with progressive active:

```
1. store = MemoryStore(); repo = current_repo()
2. head_sha = fetch_pr_head_sha(pr)
3. watermark = await store.get_watermark(repo, pr)     # None first time
4. if watermark is None:
       diff = fetch_diff(pr=pr)                        # full review, first time
       mode = "full (no prior review)"
   else:
       status, patch = fetch_incremental_diff(owner, name, watermark, head_sha)
       if status == "identical":
           print empty ReviewResult in selected format
           _status(f"No new commits since last review ({watermark[:7]}).")
           exit 0
       elif status == "ahead":
           diff = patch
           _status(f"Reviewing N new commit(s) since {watermark[:7]}...")
       else:  # diverged or IncrementalDiffError
           diff = fetch_diff(pr=pr)
           _status(warning: watermark no longer an ancestor; full review)
5. ... gather context, run engine, print result (unchanged) ...
6. persist findings  (unchanged)
7. await store.set_watermark(repo, pr, head_sha)       # only after success
8. post to PR if --post  (unchanged)
```

### Flag / config interactions

- `--full` → `fetch_incremental_diff` never called; full `fetch_diff(pr=...)`
  used. Watermark is still advanced to current `head_sha` after success, so the
  next progressive run starts fresh from here.
- `config.progressive == false` → full review always; no watermark reads or
  writes.
- `--no-memory` or `config.memory == false` → no store → no watermark; always
  full review. One-line `_status` notes it: `memory disabled; running full
  review (progressive review needs memory)`.
- First review of a PR (no watermark) → behaves exactly like today (full
  review). Zero behavior change for first-time users.

### Commit count

The compare JSON includes `total_commits`; surfaced in the CLI status line and
the server log (`commit_count` field).

## Server-mode wiring

In `_run_review_for_job` (worker.py), inserted after
`config = await _load_safe_config(...)` and before the current
`fetch_pr_diff`:

```
repo_key = f"{job.owner}/{job.repo}"
watermark = await store.get_watermark(repo_key, job.pr_number)   if store else None
incremental = None
if config.progressive and store is not None and watermark is not None:
    if watermark == job.head_sha:
        # no-op webhook (metadata-only push, etc.)
        return ReviewOutcome(conclusion="success",
                             title="No new commits since last review",
                             summary=f"Head {job.head_sha[:7]} unchanged.")
    try:
        patch, status = await github.compare_diff(token, owner, repo,
                                                  watermark, job.head_sha)
    except Exception:
        logger.warning("compare_failed", ...)
        patch, status = None, "diverged"
    if status == "ahead":
        incremental = patch
    elif status == "identical":
        return ReviewOutcome(conclusion="success",
                             title="No new commits since last review", ...)
    # else diverged -> incremental stays None -> full review below

diff = incremental if incremental is not None \
       else await github.fetch_pr_diff(token, owner, repo, job.pr_number)
```

Watermark is written (after success + finding persistence, same spot as today)
via `await store.set_watermark(repo_key, job.pr_number, job.head_sha)`.

### Notes

- `config.progressive` (default `True`) is read from `.superseded.yaml` on the
  default branch via the existing `_load_safe_config` path.
- `store is None` (defensive — the `serve` command wires a store, but in case)
  → full review, no error.
- The no-op path (`identical` or `watermark == head_sha`) completes the check
  run as `success`, posts nothing, persists nothing. Reviewing the same SHA
  twice adds no value.
- Structured logs: `review_progressive` event with
  `mode: incremental|full|noop`, `base_sha`, `head_sha`, `commit_count`;
  `review_skipped_noop` on the no-op path.

## Config field

```python
# Config (pydantic, config.py)
progressive: bool = True
```

Read by both CLI (gate) and server (`_load_safe_config`). Existing
`.superseded.yaml` files without the key get the default via pydantic — no
migration needed.

## Observability

CLI `_status` lines (stderr):

- `Reviewing N new commit(s) since abc1234...`
- `Running full review` (first run / `--full` / `config.progressive=false`)
- `No new commits since last review (abc1234).`
- `watermark <sha> no longer an ancestor; falling back to full review`
- `memory disabled; running full review (progressive review needs memory)`

Server structured logs: `review_progressive` (`mode`, `base_sha`, `head_sha`,
`commit_count`) and `review_skipped_noop`. Compare-API failures emit
`compare_failed`.

## Edge cases

| Case | Behavior |
|---|---|
| First review of a PR (no watermark) | Full review; watermark written after success. |
| `--full` | Full review; watermark advanced to current `head_sha` after success. |
| `--no-memory` / `config.memory == false` | Full review; note emitted; no watermark read/write. |
| Same SHA re-reviewed (no new commits) | CLI: empty result, exit 0. Server: `success` check run, "No new commits" title. |
| Rebase / force-push / squash (`diverged`/`behind`) | Full review with warning; watermark advanced to new head. |
| `gh api` / compare endpoint failure | Warning → full review. Progressive never blocks. |
| PR closed & reopened, or PR number reused | Stored SHA eventually no longer in history → `diverged` → full review. Self-healing. |
| Review fails (engine error / timeout) | Watermark NOT advanced; next run retries from same base. |
| `config.progressive == false` | Full review always; no watermark reads or writes anywhere. |
| Multi-commit push | `ahead` returns aggregate patch for all new commits; reviewed as one diff. |

## Testing

All `gh` / `aiohttp` / `subprocess` calls mocked, per existing conventions
(`tests/test_integration.py`, `tests/test_diff.py`). No real network.

**1. Unit — `MemoryStore` watermark** (`tests/test_watermark.py` or extend
`tests/test_memory.py`):

- `get_watermark` returns `None` for unknown `(repo, pr)`.
- `set_watermark` writes; `get` returns the SHA.
- `set_watermark` twice for same key replaces (INSERT OR REPLACE).
- Migration: DB with only existing tables → open with new code →
  `review_watermarks` queryable.

**2. Unit — `incremental.py`** (`tests/test_incremental.py`, `subprocess.run`
mocked):

- `status == "ahead"` → returns patch.
- `status == "identical"` → `(None, "identical")`.
- `status == "diverged"` → `(None, "diverged")`.
- `status == "behind"` → normalized to `"diverged"`.
- `gh` raises `CalledProcessError` → `IncrementalDiffError` raised.
- Argv shape: `gh api repos/{o}/{r}/compare/{base}...{head}` built correctly
  (owner/repo split from slug, SHAs verbatim).

**3. Integration — CLI `_run_review`** (extend `tests/test_integration.py`):

- No watermark → full review; `set_watermark` called with `head_sha`.
- Watermark + `ahead` → incremental diff used; `fetch_diff(pr=...)` NOT called;
  watermark advanced.
- Watermark + `identical` → empty result, exit 0, engine never invoked,
  watermark unchanged.
- `diverged` → full `fetch_diff`, warning on stderr, watermark advanced.
- `--full` → `fetch_incremental_diff` never called, full diff, watermark still
  advanced.
- `--no-memory` → full review, no store interaction.
- Engine failure → watermark NOT advanced.

**4. Integration — server worker** (extend `tests/test_worker.py`):

- Watermark + `ahead` → `compare_diff` called, `fetch_pr_diff` skipped, check
  run `success`.
- `identical` → check run completed "No new commits", no review, no findings.
- `diverged` → full `fetch_pr_diff`, watermark advanced.
- `config.progressive == false` → always full review.
- `store is None` → full review, no error.

## Files touched

- `src/superseded/memory/store.py` — `review_watermarks` table, migration, two
  async methods.
- `src/superseded/incremental.py` — **new** — `fetch_incremental_diff`,
  `IncrementalDiffError`.
- `src/superseded/diff.py` — `fetch_pr_head_sha` helper.
- `src/superseded/config.py` — `progressive: bool = True`.
- `src/superseded/cli.py` — `--full` flag, progressive flow in `_run_review`.
- `src/superseded/server/worker.py` — progressive flow in `_run_review_for_job`.
- `src/superseded/server/github.py` — `compare_diff` method.
- `tests/test_watermark.py`, `tests/test_incremental.py` — **new**.
- `tests/test_integration.py`, `tests/test_worker.py` — extended.

## Risks

- **GitHub compare API rate limits / availability.** Mitigated by the
  fall-back-to-full policy: any error degrades to today's behavior.
- **Watermark staleness on PR reuse / number recycling.** Self-heals via the
  `diverged` path.
- **Silent full-review regression if memory is off.** Surfaced by the explicit
  `_status` note so users aren't confused about why progressive isn't active.
