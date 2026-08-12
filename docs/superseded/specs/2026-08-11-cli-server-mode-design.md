# CLI Server-Mode — Design

**Date:** 2026-08-11
**Status:** Draft

## Problem

`superseded review` always runs locally: it fetches the diff, gathers context,
and calls the provider API directly from the caller's machine, which means the
caller must hold a provider API key (`SUPERSEDED_DEEPSEEK_API_KEY` /
`SUPERSEDED_OPENAI_API_KEY` / `SUPERSEDED_ANTHROPIC_API_KEY`) and must be online
to the provider. The only existing way to drive a running review server is the
GitHub Action, which POSTs to `/review/pr` and then exits — it never returns
findings to the caller. The server posts its results to the PR via its GitHub
App instead.

There is no path that combines the ergonomics of the local CLI (print findings
to the terminal in table/json/markdown) with a centrally-administered server
(one operator holds the provider key, runs the model, owns the GitHub App). This
spec adds one: a `--server` mode on `review` that submits a job to the existing
`/review/pr` endpoint, polls a new job-status endpoint until completion, and
renders the findings locally.

## Goals / Non-goals

**Goals**

- `superseded review --server <url> --pr N` reviews a PR via a running server,
  blocks until the review completes, and prints findings through the existing
  formatters (`table` / `json` / `markdown`).
- The caller needs only the server URL and server key — **no provider API key
  on the client machine**.
- A `--no-post` flag suppresses server-side PR posting (check-run and review
  comments) so the same call can be used as a silent preview.
- Configuration precedence matches the existing `provider` / `model` pattern:
  env > flag > config file.
- Exit codes match local mode: `0` clean, `3` (`EXIT_PARTIAL_FAILURE`) when the
  result carries warnings.
- No new runtime dependencies (`httpx` is already a direct dep).

**Non-goals**

- Reviewing local diffs or files via the server (`--diff` / `--files` /
  `--staged` in server-mode). Server-mode is PR-only; it reuses the existing
  `/review/pr` endpoint which fetches the diff through the server's GitHub App.
  A future `POST /review/diff` endpoint could add this.
- Durable job history. Job status lives in memory on the server and is lost on
  restart (see Approach A in the design discussion). The findings themselves
  remain persisted in the store keyed by repo, as today.
- Streaming / partial output. The CLI polls; it does not subscribe to a stream.
- A `--no-post` equivalent for the GitHub Action. The Action continues to
  always post.
- Polling-interval or budget tunability beyond reusing `--timeout`.

## User experience

New `review` flags:

| Flag | Purpose |
|------|---------|
| `--server URL` | Base URL of the review server. Activates server-mode. |
| `--server-key KEY` | Bearer key for the server. |
| `--owner OWNER` | PR repo owner. Defaults from current git remote. |
| `--repo REPO` | PR repo name. Defaults from current git remote. |
| `--no-post` | Suppress server-side PR posting (server-mode only). |

Existing flags that also apply in server-mode: `--pr`, `--format`, `--passes`,
`--timeout`, `--config`.

Env vars (precedence env > flag > config):

- `SUPERSEDED_SERVER_URL` — also used by the GitHub Action.
- `SUPERSEDED_SERVER_KEY` — also used by the Action.

Server-mode activation: server-mode is active when `--server`, the env var, or
the config-file `server:` field resolves to a non-empty URL. The local pipeline
(`ReviewEngine`, provider key lookup, diff fetch, context gathering, memory
store) is skipped entirely.

Server-mode constraints:

- Requires `--pr`. Combining `--server` with `--diff` / `--files` / `--staged`
  → error, exit 2.
- `--owner` / `--repo` default from `current_repo()` (returns `owner/repo` from
  the git remote). If no remote resolves and the flags are absent → error,
  exit 2.
- `--post` is meaningless in server-mode (the server's posting is controlled
  by `--no-post`, not `--post`). Passing `--post` with `--server` prints a
  one-line warning to stderr and is otherwise ignored; it is **not** an error.
- The `--no-memory`, `--no-static`, `--no-usage`, `--no-conventions`,
  `--no-specs`, `--graph`, `--verify`, `--full` flags are accepted but have no
  effect in server-mode (the server controls all context-gathering). A single
  consolidated warning listing the ignored flags is printed to stderr; they are
  documented as server-operator-controlled.

Example:

```bash
# Review PR 123 via a server; findings print locally; server also posts to the PR
SUPERSEDED_SERVER_KEY=... uv run superseded review --server https://rev.example.com --pr 123

# Same, but suppress PR posting (silent preview)
SUPERSEDED_SERVER_KEY=... uv run superseded review --server https://rev.example.com --pr 123 --no-post --format json
```

## Configuration & precedence

New fields on the local `Config` model in `src/superseded/config.py`:

```python
server: str | None = None
server_key: str | None = None
```

New helpers in `cli.py`, mirroring `resolve_provider` / `resolve_model`:

```python
def resolve_server(server_flag: str | None, config: Config) -> str | None: ...
def resolve_server_key(key_flag: str | None, config: Config) -> str | None: ...
```

Both honor env (`SUPERSEDED_SERVER_URL`, `SUPERSEDED_SERVER_KEY`) > flag >
config, returning `None` when unset.

`superseded init` is extended to probe `SUPERSEDED_SERVER_URL` /
`SUPERSEDED_SERVER_KEY` and report their presence in its output; they are **not
written** into `.superseded.yaml`. This is non-interactive, like the existing
`gh` / provider-key probes.

## Data flow

```
superseded review --server <url> --pr N [--format json] [--no-post]
  │
  ├─ POST <url>/review/pr
  │     Authorization: Bearer <server_key>
  │     Content-Type: application/json
  │     { "owner": ..., "repo": ..., "pr_number": N, "passes"?: "...", "post"?: false }
  │
  │   200 → { "status": "enqueued", "job_id": "abc123" }
  │   401 → "Error: invalid server key"                              exit 2
  │   403 → "Error: repository not authorized for this installation" exit 2
  │   409 → "Error: GitHub App not installed on this repository"     exit 2
  │   422 → "Error: missing or invalid field: ..."                   exit 2
  │   429 → "Error: server review queue full"                        exit 1
  │   501 → "Error: server has no api_key configured"                exit 2
  │   502 → "Error: server failed to fetch PR info: ..."             exit 1
  │   5xx / network → "Error: ..."                                   exit 1
  │
  ├─ poll GET <url>/review/jobs/abc123  (every 2.0s, budget = --timeout)
  │     Authorization: Bearer <server_key>
  │
  │   200 { "status": "queued" | "running" }                         continue
  │   200 { "status": "completed", "result": { ... ReviewResult ... } } → render
  │   200 { "status": "failed", "error": "..." }                     exit 1
  │   401 → "Error: invalid server key"                              exit 2
  │   404 → "Error: job disappeared (server restart?)"               exit 1
  │   5xx / network → "Error: ..."                                   exit 1
  │
  └─ render result via format_table | format_json | format_markdown
      → exit 0  (clean)
      → exit 3  (EXIT_PARTIAL_FAILURE, when result.warnings non-empty)
```

- **Poll interval:** hardcoded `2.0` seconds. No flag.
- **Poll budget:** the existing `--timeout` flag is reused as the total
  wall-clock budget in server-mode (default 600s). Its help text is updated to
  "Timeout in seconds (per pass locally; total poll budget in server-mode)".
  On timeout the CLI exits 1 with a clear message; the server-side job keeps
  running and may still post to the PR (unless `--no-post`).
- **Ctrl-C** during polling exits 130. The server-side job is unaffected.
- A status line is printed while polling (`Review in progress…`, updated in
  place) so the CLI is not silent for minutes.

## Server endpoint: `GET /review/jobs/{job_id}`

Added to `src/superseded/server/app.py`. Auth is identical to `/review/pr`:

- `501` with body `"API key not configured on this server."` when
  `config.api_key` is unset.
- `401` when `Authorization: Bearer <key>` does not match `config.api_key`
  (`hmac.compare_digest`, constant-time).

Responses:

- `200` — `{ "status": "...", "result"?: {...}, "error"?: "..." }`
  - `status` is one of `queued`, `running`, `completed`, `failed`.
  - `result` is the serialized `ReviewResult` (the same shape
    `format_json`/`format_markdown` consume locally) and is present only when
    `status == "completed"`.
  - `error` is present only when `status == "failed"`.
- `404` — `{"detail": "Unknown or evicted job_id."}` for an id not in the
  registry.

Authorization is scoped to the server-wide api_key (same as `/review/pr`); no
per-job ownership token is introduced. A holder of the api_key can already
trigger reviews against any authorized installation, so polling any job_id
grants no additional capability.

## Worker job registry

In-memory, on `ReviewWorker` (`src/superseded/server/worker.py`).

New dataclass:

```python
@dataclass
class JobStatus:
    job_id: str
    status: str  # "queued" | "running" | "completed" | "failed"
    result: ReviewResult | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
```

New state on `ReviewWorker`, **unlocked** (see rationale below):

```python
self._jobs: dict[str, JobStatus] = {}
```

The registry is intentionally not guarded by `self._lock`. It is touched only
from the worker's single asyncio event loop — `enqueue`, `_run_task`, `_process`,
and `_record_job` are all invoked on that one loop, and Python dict get/set are
atomic under the GIL within a single thread. The status endpoint's
`worker.get_job_status(job_id)` is a plain dict lookup with no await in between,
so there is no interleaving point where a coroutine swap could observe a
half-written entry. `_record_job`'s docstring documents this single-loop
invariant.

**This is a deliberate simplification that breaks under multi-worker uvicorn.**
Running the server with `--workers N` spawns N processes, each with its own
in-memory `_jobs` registry; a job enqueued by process A cannot be polled from
process B (the poll sees `404`). The server must run with a single uvicorn worker
for CLI polling to find the submitted job — see the deployment note in
`README.md`.

Lifecycle updates:

- `enqueue(job)` records `JobStatus(job_id=job.job_id, status="queued")`
  before `queue.put_nowait(job)`. If the queue is full, no status is recorded.
- `_run_task(job)` sets `status="running"` before awaiting `_process(job)`.
- `_process(job)` **records the terminal status itself** and returns `None`
  (as it did before this feature). It calls
  `self._record_job(job.job_id, "completed", result=result)` on success, and
  `self._record_job(job.job_id, "failed", error=...)` on every internal failure
  path (token-fetch failure, check-run creation failure, the broad
  `except Exception` around the review, and the `--no-post` equivalents), after
  performing its existing check-run update. `_run_task` therefore does not need
  to infer status from `_process`'s return value or exceptions — the registry is
  the signal, written from inside `_process`.   `CancelledError` still
  propagates and is handled by `_run_task`'s `except asyncio.CancelledError`
  branch, which records `"failed"` with error `"cancelled"` **only if the job is
  not already terminal** (so a cancel landing just after `_process` recorded
  `"completed"` does not downgrade it).
- After `_process` returns (or raises `CancelledError`), the terminal
  `JobStatus` (with `completed_at = time.time()`) is already in the registry,
  written by `_process` (or by `_run_task`'s cancel branch).
- On each registry insert, if `len(self._jobs) > 1000`, the oldest entries
  by `created_at` are evicted. Bounded memory.

The status endpoint reads `self._jobs.get(job_id)` (unlocked — single event
loop, see above) and serializes `result` via the existing `ReviewResult` model
(the same shape the local `format_json` consumes).

### `_run_review_for_job` refactor

Today `_run_review_for_job` returns `ReviewOutcome(conclusion, title, summary)`
— it does not surface the `ReviewResult` (which carries `findings`,
`warnings`, `summary`, `dropped_findings`). To populate `JobStatus.result`,
the signature changes to return both:

```python
async def _run_review_for_job(...) -> tuple[ReviewOutcome, ReviewResult]: ...
```

`_process` unpacks the tuple and uses the `ReviewOutcome` for the check-run
(as today). It returns the `ReviewResult` in its `(status, result, error)`
triple so `_run_task` can store it on `JobStatus`. All call sites
(`server/worker.py` only) are updated.

## `--no-post` worker integration

The server's `/review/pr` path always posts today (check-run + review
comments). `--no-post` adds an opt-out.

**Wire changes:**

- `ReviewJob` gains `post: bool = True`.
- `/review/pr` reads an optional `post` field from the JSON body (default
  `true` — so the GitHub Action's behavior is unchanged, since it does not
  send `post`). Sets `job.post` accordingly.
- `_process(job)`:
  - When `job.post` is `False`, skips `create_check_run` / `update_check_run`
    entirely and skips the review-comment posting step inside
    `_run_review_for_job`. The review still runs; the registry still records
    the result so the CLI can fetch it.
  - When `job.post` is `True` (default), behavior is unchanged from today.

**CLI side:** `--no-post` adds `"post": false` to the request body. Absent
the flag, the body omits `post` (server defaults to `true`).

**Why skip the check-run too:** a `--no-post` caller wants no trace on the PR.
Keeping the check-run would leave a visible "Superseded Review" entry. The
registry, not the check-run, is the CLI's status signal in server-mode, so
dropping the check-run loses nothing for this path.

## Error handling summary

| Scenario | Exit |
|----------|------|
| `--server` combined with `--diff`/`--files`/`--staged` | 2 |
| Server-mode without `--pr` | 2 |
| No `--owner`/`--repo` and no resolvable git remote | 2 |
| `--server` set but `--server-key` / env / config all unset | 2 |
| Submit 401 / 403 / 409 / 422 / 501 | 2 |
| Submit 429 (queue full) | 1 |
| Submit 502 (PR info fetch fail) / 5xx / network error | 1 |
| Poll 404 (job evicted / server restarted) | 1 |
| Poll timeout (`--timeout` budget exhausted) | 1 |
| Job `status: "failed"` | 1 |
| Clean result, no warnings | 0 |
| Result with warnings | 3 (`EXIT_PARTIAL_FAILURE`) |
| Ctrl-C during polling | 130 |

All error messages go to stderr; the rendered result goes to stdout (so
`superseded review --server ... --format json | jq` keeps working).

## Documented deviations

Shipped behavior differs from the text above in ways that are intentional but
were decided during implementation:

- **In-memory registry eviction may drop in-flight jobs under heavy load.**
  The `JobStatus` registry is capped at 1000 entries and evicts the oldest by
  `created_at` on insert. Under sustained load a long-running job can be
  evicted before it finishes; the CLI then sees `404` ("Unknown or evicted
  job_id.") while the review itself may still complete and post to the PR
  server-side. This is the accepted cost of the bounded in-memory design.
- **`completed` results are delivered once via polling.** Each `GET
  /review/jobs/{job_id}` response carries the full serialized `ReviewResult`
  on every poll; the CLI stops polling as soon as it observes the terminal
  state, so a result is consumed exactly once per client. There is no replay
  or retry-after-completion mechanism.
- **`superseded init` reports server env vars but does not write them.** See
  "Configuration & precedence" — `SUPERSEDED_SERVER_URL` /
  `SUPERSEDED_SERVER_KEY` presence is echoed to the user, never persisted.
- **Server-mode activation warnings.** Enabling server-mode via the config
  file `server:` key or the `SUPERSEDED_SERVER_URL` env var prints a warning
  to stderr (so the user can force local review by unsetting it); passing
  `--server` explicitly prints none.
- **Status line emitted per non-terminal poll response.** `poll_review` calls
  its `on_status` callback once for every non-terminal (queued/running) poll
  response with `Review in progress (status: …)`, in addition to the single
  "Review enqueued" line emitted right after submit. This keeps the CLI from
  being silent for the multi-minute poll.

## Testing

- **`tests/test_server_jobs_endpoint.py`** (new): status transitions
  (`queued` → `running` → `completed` with result; `failed` with error);
  `404` for unknown / evicted `job_id`; `401` for missing/wrong key; `501`
  when `config.api_key` unset; registry eviction at the 1000-entry cap.
- **`tests/test_server_worker.py`** (extend): registry updated at each
  lifecycle stage; `_run_review_for_job` returns
  `(ReviewOutcome, ReviewResult)`; `post=False` jobs skip check-run creation
  and comment posting; `post=True` (default) jobs behave exactly as today.
- **`tests/test_server_app.py`** (extend): `/review/pr` honors the `post`
  field; default is `true` when absent (Action compatibility).
- **`tests/test_cli_server_mode.py`** (new): end-to-end via
  `httpx.MockTransport` —
  - submit → poll → render for `table`, `json`, `markdown`;
  - exit codes: `0` clean, `3` with warnings, `1` on poll timeout /
    `status:"failed"` / 404 / network error, `2` on submit auth/validation
    failures and on bad flag combinations;
  - `--server` + `--diff` errors; `--server` without `--pr` errors; no remote
    and no `--owner`/`--repo` errors;
  - `--no-post` sends `"post": false`;
  - `--post` + `--server` warns;
  - config precedence: env > flag > config for both `server` and `server_key`.
- All HTTP is mocked (`httpx.MockTransport` / `respx`-style, matching the
  pattern already used in `tests/test_server_github.py`). No real network, no
  real server process, no provider calls.

## Open questions / future work

- **Local diff via server.** A `POST /review/diff` endpoint accepting a raw
  diff would let `--server` + `--diff` work without a GitHub App context. Out
  of scope here.
- **Durable job history.** Persisting `JobStatus` rows in the store
  (`MemoryStore` + `PostgresStore`) would survive restarts and enable a
  `superseded jobs list` / `superseded jobs show <id>` CLI. Trivial migration
  from the in-memory registry when needed.
- **Per-job ownership tokens.** Returning a single-use polling token in the
  enqueue response (instead of reusing the server-wide api_key for polling)
  would tighten the auth model if the api_key is ever treated as
  lower-trust than the caller. Not needed while the api_key authorizes both
  enqueue and poll.
- **Streaming.** Server-Sent Events on `/review/jobs/{job_id}` would let the
  CLI print per-pass progress (`security: done`, `correctness: running`, …)
  instead of a single status line. Deferred.
