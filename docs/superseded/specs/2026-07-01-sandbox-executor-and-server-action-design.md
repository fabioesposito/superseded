# Sandbox Executor & Server-Mediated Action — Design

**Date:** 2026-07-01
**Status:** Approved

## Problem

Superseded runs each review pass by shelling out **directly** to an AI CLI
(`claude-code`, `codex`, `opencode`) via `subprocess.run` in
`review/engine.py`. In server mode this happens in-process inside the `api`
container; in the GitHub Action it happens in a Docker image that bundles every
AI CLI and runs `superseded review --pr` locally. Two architectural changes are
required:

1. **Agent handover must happen on a Docker Sandbox** (`sbx` microVM), not by
   shelling out to the agent CLI on the host/container. Each agent invocation
   runs inside an isolated microVM with its own filesystem and network.
2. **The GitHub Action must not build an image**; instead it sends the review
   request to a running server whose location is defined via an environment
   variable. The server performs the review (in a sandbox) and posts the
   results back to GitHub.

## Goals / Non-goals

**Goals**

- Introduce a pluggable **agent executor** so that "run the agent command" is
  an interchangeable backend: a direct `subprocess` backend (today's behavior)
  and a new **`sbx` sandbox** backend.
- Server-mediated reviews run agents in `sbx` sandboxes by default; the local
  developer CLI keeps the direct-subprocess behavior, and either side is
  switchable.
- One sandbox per review job; the existing 5 concurrent passes run as
  `sbx exec` invocations into that single sandbox.
- The GitHub Action becomes a thin HTTP client that POSTs to a server
  (`SUPERSEDED_SERVER_URL` / `SUPERSEDED_SERVER_KEY`, env-over-input precedence),
  fire-and-forget; the server posts results via its GitHub App.
- No change to agent prompts, `Agent.build_command()` / `parse_output()`, the
  merger, context gathering, or the worker's GitHub/store logic — the sandbox is
  purely a swap-in execution substrate.

**Non-goals**

- A warm sandbox pool, per-pass sandboxes, or sandbox-in-container (KVM
  passthrough into the `api` container).
- A cloud/remote `sbx` API — none exists; `sbx` is a host-level CLI.
- Changing how findings are merged, how context is gathered, or how the local
  CLI `review` command renders output.
- Publishing server images to a registry.

## Constraints discovered

- **Docker Sandboxes (`sbx`) is a host-level microVM tool.** On Linux it
  requires **KVM** (`lsmod | grep kvm`), the `docker-sbx` package, the operator
  in the `kvm` group, and `sbx login`. It **cannot** run on a GitHub Actions
  runner (no KVM) and **cannot** run inside a normal container (no nested
  virtualization). There is no cloud API — it is a local CLI that mounts a
  local workspace. (See <https://docs.docker.com/ai/sandboxes/>.)
- Therefore the review server — which orchestrates sandboxes — must run on a
  **KVM-capable host**, not in the existing `api` container and not on a GH
  Actions runner. The containerized `api` target remains valid only for the
  **non-sandbox** (direct-subprocess) server path.
- **`sbx create <agent> <workspace>`** pre-installs the named agent CLI inside
  the microVM; `sbx exec <name> -- <cmd>` runs a command inside a running
  sandbox; `sbx rm <name>` tears it down; `sbx cp` copies files between host and
  sandbox (one side must be `SANDBOX:PATH`). Superseded's agent names need a
  small map to `sbx` agent names: `claude-code`→`claude`, `opencode`→`opencode`,
  `codex`→`codex` (all three are supported `sbx` agents).
- **Credentials in a sandbox are injected by a host proxy, not passed as env.**
  `sbx secret set -g anthropic` / `-g openai` stores provider keys in the host
  OS keychain; `sbx` injects them into the sandbox's outbound API calls so keys
  never enter the microVM. The sandbox network policy must allow the provider
  hosts.
- **The existing `POST /review`** (`server/app.py`) is installation-scoped: it
  requires an `installation_id` in the body and looks it up in the store. A
  GitHub Action does not know its installation id, so the Action path needs a
  different entry point that resolves the installation from `owner`/`repo`.
- **The worker already checks out the repo to a per-job temp dir**
  (`repo_manager.job_dir`, cleaned in a `finally`). That checkout is a natural
  sandbox workspace; `SandboxExecutor` teardown (`sbx rm`) runs inside
  `engine.review`'s `with` block, which completes before the worker's
  `finally: repo_manager.cleanup(tmp_dir)` — so the sandbox is always removed
  before its mount directory disappears.
- **GitHub Actions `using: docker`** builds the referenced image in one build.
  Switching the Action to `using: composite` removes that build entirely; the
  composite step runs natively on the runner where `curl` and `jq` are
  available.

## Design

### Architecture & data flow

```
PR event
  └─ GitHub Action (composite, no Docker build)
       reads SUPERSEDED_SERVER_URL + SUPERSEDED_SERVER_KEY (env > inputs)
       POST {owner, repo, pr_number, passes?}  →  ${URL}/review/pr
       Authorization: Bearer ${KEY}
       receives {job_id}, exits immediately
            │
            ▼
   Superseded server (FastAPI, on KVM host with `sbx` installed)
     · auth via api_key
     · resolve GitHub App installation for owner/repo
       (GET /repos/{o}/{r}/installation) → installation token
     · fetch PR head/base sha → enqueue ReviewJob
            │
            ▼
   ReviewWorker (asyncio)
     · create check run "in_progress" (GitHub App token)
     · checkout repo → temp dir (existing checkout_repo)
     · load safe config + gather context (unchanged)
     · ┌─ SandboxExecutor session ─────────────────────────────┐
     · │ sbx create --name <job> <agent> <repo_checkout>        │
     · │ for each pass (concurrent ThreadPoolExecutor):         │
     · │   sbx exec <name> -- <agent headless cmd>  (stdin)     │
     . │   → JSON findings on stdout                            │
     · │ sbx rm <name>                                          │
     · └────────────────────────────────────────────────────────┘
     · merge findings (unchanged merger)
     · post review + update check run (GitHub App token)
     · record findings/watermark to store
```

**Local CLI path (unchanged substrate):** `superseded review` builds a
`SubprocessExecutor` (shells out to AI CLIs directly), unless `--sandbox` /
`SUPERSEDED_SANDBOX` / config opts into `SandboxExecutor`. Same engine, prompts,
and merger.

**Key invariant:** agent prompts, `Agent.build_command()`, `parse_output()`,
the merger, and the worker's GitHub/store logic are all untouched. The sandbox
is purely a swap-in execution substrate behind the executor interface.

### Component map

| Status | File | Change |
|---|---|---|
| New | `src/superseded/review/executor.py` | `AgentExecutor`/`Session` Protocols, `AgentRunError`, `SubprocessExecutor`, `SandboxExecutor`, `make_sandbox_executor()` |
| Mod | `src/superseded/review/engine.py` | accept an `executor`; run passes through a `Session` instead of inline `subprocess.run`; `is_available()` guard delegated to `executor.available()` |
| Mod | `src/superseded/server/worker.py` | build a `SandboxExecutor` for the job (when enabled) and pass it to `engine.review` |
| Mod | `src/superseded/server/app.py` | new `POST /review/pr` (owner/repo/pr_number) + installation resolution |
| Mod | `src/superseded/server/github.py` | `resolve_installation(owner, repo)` |
| Mod | `src/superseded/server/config.py` | sbx/executor config fields + `from_env()` |
| Mod | `src/superseded/config.py`, `src/superseded/cli.py` | local `sandbox` toggle + executor selection |
| Rewrite | `action.yml` | composite action: POST to server (env > inputs); drop Docker execution |
| Remove | `docker/entrypoint.sh` (+ its `COPY` in the `cli` Dockerfile stage) | Action-specific orchestration, no longer referenced |
| Mod | `compose.yml` | add `SUPERSEDED_SANDBOX` env (default off) |

### The executor abstraction (`review/executor.py`)

A `Session` runs one agent command (prompt on stdin → JSON stdout); an
`AgentExecutor` opens a session rooted at a working directory. The engine opens
**one session per review** and the existing `ThreadPoolExecutor` fans the 5
passes out as concurrent `session.run()` calls against it.

```python
class AgentRunError(RuntimeError):
    """subprocess/sbx failures surface as this (preserves today's messages)."""

class Session(Protocol):
    def __enter__(self) -> "Session": ...
    def __exit__(self, *exc) -> None: ...                 # teardown (sbx rm)
    def run(self, cmd: list[str], prompt: str, *, timeout: int) -> str:
        """Run cmd with prompt on stdin; return stdout. Raise AgentRunError."""

class AgentExecutor(Protocol):
    def available(self, agent: Agent) -> bool: ...        # PATH / sbx presence
    def session(self, cwd: str, *, env: dict[str, str] | None = None) -> Session: ...
```

**`SubprocessExecutor`** — lifts today's `subprocess.run` out of
`engine.run_pass` verbatim: same `FileNotFoundError`/`TimeoutExpired`/non-zero
→ `AgentRunError` messages, same `env` semantics (`None` inherits, a `dict`
replaces). `available()` delegates to `agent.is_available()`. `session()` is a
thin wrapper holding `cwd`/`env`. Behavior identical to today.

**`SandboxExecutor`** — configured with `binary="sbx"`, an agent-name map
(`claude-code`→`claude`, `opencode`→`opencode`, `codex`→`codex`), `timeout`,
`keep_on_error=False`, a `name`, and an `io_mode` (`"exec"` default or `"cp"`).

- `available()`: `shutil.which("sbx")` is on PATH (the agent itself lives
  *inside* the sandbox, so no host PATH check for it).
- `session().__enter__`: name `superseded-<id>`; run
  `sbx create --name <name> <sbx_agent> <cwd>` (direct mode; `cwd` is the
  per-job temp checkout, discarded afterward). Poll until running
  (readiness confirmed by the de-risking spike).
- `session().run`: invoke
  `subprocess.run(["sbx", "exec", name, "--", *cmd], input=prompt,
  capture_output=True, text=True, timeout=timeout)` → return stdout; translate
  not-found/timeout/non-zero to `AgentRunError`.
- `session().__exit__`: `sbx rm <name>` in `finally` (kept on error only when
  `keep_on_error`, for debugging).

**Concurrency:** one sandbox per job; the 5 pass threads each call
`session.run()` → 5 concurrent `sbx exec` invocations into the *same* microVM.
Safe — passes are read-only.

**Credentials (server):** provider keys are **not** passed through Python. The
host has `sbx secret set -g anthropic` / `-g openai` configured; `sbx`'s host
proxy injects them into the sandbox's outbound API calls, so keys never enter
the microVM. `--model` travels via `build_command()` as today. Host `env` is
ignored by the sandbox backend (proxy model); the `SubprocessExecutor` fallback
continues to use it.

**Engine integration:** `ReviewEngine.review(...)` gains
`executor: AgentExecutor | None = None` (default `SubprocessExecutor()`); it
opens `executor.session(cwd, env=env)` once, and `run_pass` calls
`sess.run(self.agent.build_command(), prompt, timeout=timeout)` then
`agent.parse_output(...)`. The host-side `is_available()` guard moves to
`executor.available(self.agent)`. Per-pass failure remains **skipped + warned,
not fatal** (today's behavior).

**`make_sandbox_executor(agent, name, timeout, keep_on_error, binary="sbx",
io_mode="exec")`** is the shared constructor used by both the server and the
local CLI, so SandboxExecutor configuration is defined once.

### Server-side changes

**New endpoint `POST /review/pr`** (additive — the existing
installation-scoped `POST /review` and `/webhook` stay untouched). This is the
Action's target.

- Auth: `Authorization: Bearer <config.api_key>` (same pattern as the existing
  `/review`). `501` if no api_key configured, `401` if mismatched.
- Body: `{ "owner", "repo", "pr_number", "passes"? }` (`passes` optional,
  forwarded to the engine).
- Flow: resolve the GitHub App **installation** for the repo → get installation
  token → `fetch_pr_info` (head/base sha) → build & enqueue the same
  `ReviewJob` the worker already consumes. Returns
  `{ "status": "enqueued", "job_id" }`.
- Requires the GitHub App to be installed on the repo (documented precondition
  for the Action path).

**`server/github.py`:** add `resolve_installation(owner, repo) -> int | None` —
`GET /repos/{owner}/{repo}/installation` with the app JWT; returns the
installation id or `None` (404 → app not installed → `409` to the Action).

**`server/worker.py`:** the only behavioral change is in
`_run_review_for_job`, right where `engine.review(...)` is dispatched. After
`_load_safe_config` determines `config.agent`, build the executor:

```python
executor = (
    make_sandbox_executor(agent=SBX_AGENT_MAP[config.agent],
                          name=f"superseded-{job.job_id}",
                          timeout=cfg.sandbox_timeout,
                          keep_on_error=cfg.sandbox_keep_on_error,
                          binary=cfg.sbx_binary, io_mode=cfg.sandbox_io_mode)
    if cfg.sandbox_enabled else None
)
result = await asyncio.to_thread(engine.review, ..., executor=executor)
```

`engine.review` defaults `executor=None` → `SubprocessExecutor()`, so the
local-CLI and webhook/manual paths are unaffected. Everything around it —
check-run lifecycle, context gathering, posting, store recording,
progressive/incremental, learned-review reflection — is unchanged.

(The snippet's `cfg` is the sandbox settings — `sbx_binary`,
`sandbox_timeout`, `sandbox_keep_on_error`, `sandbox_io_mode` — passed into
`ReviewWorker.__init__` alongside the existing `server_agent`/`server_model`,
sourced from `ServerConfig` at server construction time. `_load_safe_config`
still produces the review `config` that supplies `config.agent`; both are in
scope in `_run_review_for_job`.)

**Failure mode:** if `sandbox_enabled` is true but `sbx` is not on PATH
(`SandboxExecutor.available()` false), the job **fails loudly** (check run →
`failure`, "sandbox unavailable") rather than silently running un-sandboxed.
There is no silent fallback.

**`ServerConfig` additions** (env-overridable, mirroring `SUPERSEDED_*`):

| Field | Default | Env |
|---|---|---|
| `sandbox_enabled: bool` | `True` | `SUPERSEDED_SANDBOX` |
| `sbx_binary: str` | `"sbx"` | `SUPERSEDED_SBX_BINARY` |
| `sandbox_timeout: int` | `600` | `SUPERSEDED_SANDBOX_TIMEOUT` |
| `sandbox_keep_on_error: bool` | `False` | `SUPERSEDED_SANDBOX_KEEP_ON_ERROR` |
| `sandbox_io_mode: str` | `"exec"` | `SUPERSEDED_SANDBOX_IO_MODE` |

`from_env()` extended accordingly; `require_configured()` unchanged.

### GitHub Action rewrite

`action.yml` switches from `runs.using: docker` to **`runs.using: composite`** —
a single `curl` step that POSTs to the server. No image build, no `gh`, no AI
CLIs in the Action at all.

**Inputs** (env vars take precedence over inputs, matching the repo's
`env > flag > config` convention):

```yaml
inputs:
  server-url:
    description: "Base URL of the running Superseded server (e.g. https://reviews.example.com). Env SUPERSEDED_SERVER_URL overrides this."
    default: ""
  server-key:
    description: "Bearer API key for the server. Env SUPERSEDED_SERVER_KEY overrides this. Map from a secret."
    default: ""
  passes:
    description: "Comma-separated passes (optional; server default applies if omitted)."
    default: ""
```

The previous `agent`, `model`, `anthropic_api_key`, `openai_api_key` inputs are
**removed** — the server owns agent/model/credentials now.

**The step** (native bash; `curl` + `jq` ship on `ubuntu-*` runners):

```bash
URL="${SUPERSEDED_SERVER_URL:-${INPUT_SERVER_URL}}"
KEY="${SUPERSEDED_SERVER_KEY:-${INPUT_SERVER_KEY}}"
# validate URL / KEY / GITHUB_EVENT_PULL_REQUEST_NUMBER (clear error + exit 1 if missing)
owner=${GITHUB_REPOSITORY%/*}; repo=${GITHUB_REPOSITORY#*/}
body=$(jq -n --arg o "$owner" --arg r "$repo" --argjson n "$GITHUB_EVENT_PULL_REQUEST_NUMBER" \
       '{owner:$o, repo:$r, pr_number:$n}')
[ -n "$INPUT_PASSES" ] && body=$(echo "$body" | jq --arg p "$INPUT_PASSES" '. + {passes:$p}')
curl -fsS --retry 3 --retry-delay 2 --retry-connrefused \
     -X POST "$URL/review/pr" \
     -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
     -d "$body"          # prints {"status":"enqueued","job_id":...}; action exits
```

- **Fire-and-forget:** the Action prints the `job_id` and exits immediately; the
  server creates the check run and posts the review via its App.
- **PR context** comes from the runner env (`GITHUB_REPOSITORY`,
  `GITHUB_EVENT_PULL_REQUEST_NUMBER`), gated to `pull_request` events.
- **Retry:** `curl --retry` covers transient connection/5xx failures; a missing
  server/key/PR-number is a hard `exit 1` with a clear message.

**Permissions:** the Action no longer writes to GitHub, so the calling workflow
**no longer needs** `permissions: { contents: read, pull-requests: write }` (the
server's App does all writes). The description string is updated to reflect
"App must be installed on the repo" instead.

**Dockerfile/entrypoint cleanup:**

- `docker/entrypoint.sh` is **removed** (it was Action-specific: read `INPUT_*`,
  run `superseded review --pr`). The `cli` Dockerfile stage's
  `COPY docker/entrypoint.sh` line goes too.
- The `cli` stage itself stays (containerized CLI for general use:
  `ENTRYPOINT ["superseded"]`), as does the `api` stage (the server image). The
  Action simply no longer references any Dockerfile.

### Config, packaging & operational requirements

**Local config (`config.py` + `cli.py`):** add `sandbox: bool = False` to
`Config` and a `--sandbox`/`--no-sandbox` flag on `superseded review`. Toggle
precedence mirrors the existing `graph` toggle exactly:
**`SUPERSEDED_SANDBOX` env > `--sandbox`/`--no-sandbox` flag >
`.superseded.yaml` `sandbox` > default** (False locally, True on the server).
`cli.py` resolves the toggle and builds the executor via
`make_sandbox_executor(...)` (`SandboxExecutor` when on + `sbx` on PATH, else
`SubprocessExecutor`).

**No new Python dependencies.** `SandboxExecutor` shells out to the external
`sbx` binary exactly as the agents already shell out to external CLIs — no
Docker SDK, no new packages, no `pyproject.toml`/`uv.lock` change. `sbx` is
simply a new *runtime external dependency* for server sandbox mode (alongside
the existing `gh`/AI-CLI requirements).

**Two documented deployment shapes:**

1. **Host deployment (sandbox mode — the new default for the Action target).**
   `superseded serve` runs directly on the KVM-capable host (systemd unit),
   where `sbx` lives. `SUPERSEDED_SANDBOX=1` (default). This is the shape the
   GitHub Action points at.
2. **Container deployment (`compose.yml` — non-sandbox).** The existing `api`
   container keeps working for webhook/manual reviews via the in-process
   `SubprocessExecutor`. `compose.yml` gains
   `SUPERSEDED_SANDBOX: ${SUPERSEDED_SANDBOX:-0}` (default **off**, since
   containers usually lack KVM). The Dockerfile `api` target does **not**
   install `sbx`. (Sandbox-in-container is possible only with privileged +
   `/dev/kvm` + `sbx` bind-mount — documented as unsupported/advanced.)

**`compose.yml`:** add the `SUPERSEDED_SANDBOX` env line only; no structural
change.

**Operational requirements for the host/sandbox deployment** (README + this
spec):

- Ubuntu 24.04+ (or equivalent) with **KVM** enabled (`lsmod | grep kvm`),
  `docker-sbx` installed, operator in the `kvm` group, `sbx login` completed.
- Provider credentials stored once on the host keychain:
  `sbx secret set -g anthropic` and/or `-g openai` — `sbx`'s host proxy injects
  them into sandbox outbound calls, so keys never enter a microVM.
- sbx network policy permits the model-provider hosts
  (`sbx policy allow network api.anthropic.com` etc. if running Locked Down).
- Server env: existing `SUPERSEDED_APP_ID` / `WEBHOOK_SECRET` /
  `PRIVATE_KEY_PATH` / `API_KEY` / `DATABASE_URL` / TLS-or-behind-proxy, plus
  `SUPERSEDED_SANDBOX=1`, optional `SUPERSEDED_SERVER_AGENT`/`MODEL` to pin, and
  `SUPERSEDED_SANDBOX_TIMEOUT` / `_KEEP_ON_ERROR` / `_IO_MODE`.

### Testing strategy

All tests mock external binaries — **no live `sbx`, `gh`, or AI-CLI calls** (per
AGENTS.md; `sbx` is added to that list). pytest with `asyncio_mode = "auto"`.

**`tests/test_executor.py` (new — the core unit):**

- `SubprocessExecutor`: pin the exact current behavior — success returns stdout;
  `FileNotFoundError`→`AgentRunError` (PATH message); `TimeoutExpired`→
  `AgentRunError` (timeout message); non-zero→`AgentRunError` (exit-code/stderr
  message). Mock `subprocess.run`. Guards against regressions in the lifted
  logic.
- `SandboxExecutor`: mock `subprocess.run` to assert the precise command
  sequences — `sbx create --name <name> <agent> <cwd>` /
  `sbx exec <name> -- <agent headless cmd>` (with `input=prompt`) /
  `sbx rm <name>`; agent-name map; `__exit__` always `rm`s (skipped on error
  only when `keep_on_error`); `sbx`-missing/non-zero/timeout → `AgentRunError`;
  unique per-job naming. Both `io_mode` branches (`exec`-stdin and the `sbx cp`
  fallback) are tested.

**`tests/test_engine.py` (modify):** inject a `FakeExecutor` whose
`Session.run` returns canned per-pass JSON — assert one session opened, one
`run` per enabled pass with the right prompts, merger invoked, and a failing
pass (AgentRunError) skipped + warned (not fatal). Default-`None` executor →
`SubprocessExecutor`.

**Server `/review/pr` (modify server tests):** 501 (no api_key), 401 (bad
bearer), 422 (missing fields), 409 (app not installed → `resolve_installation`
None), 502 (PR fetch error), happy path (200 `{job_id}`, job enqueued, `passes`
forwarded).

**`server/github.py`:** `resolve_installation` — mock httpx: 200→id, 404→None,
other→raise.

**`server/worker.py`:** executor selection — `sandbox_enabled` + `sbx` present
→ `SandboxExecutor` (correct agent/timeout/name) passed to `engine.review`;
`sbx` absent while enabled → job fails with check-run "failure"/"sandbox
unavailable" (no silent fallback). Existing checkout/post/store tests
unchanged.

**`tests/test_cli.py` (modify):** sandbox toggle resolution
`SUPERSEDED_SANDBOX` env > `--sandbox` > config `sandbox` > default False,
asserting which executor is built (mock `shutil.which`) — mirroring the
existing `graph`/agent/model tests.

**`tests/test_action.py` (new, lightweight):** `action.yml` parses with
`using: composite` and no `docker`/`image` keys; `docker/entrypoint.sh` no
longer exists; the composite step's curl body is well-formed given env vars
(drive the script with a fake `curl`/`jq` on PATH that asserts the request
shape).

## Migration / backward compatibility

- **Breaking for Action users:** the Action now requires `server-url` /
  `server-key` (or the `SUPERSEDED_SERVER_*` env vars) and the GitHub App
  installed on the repo, and drops the `agent` / `model` / `anthropic_api_key` /
  `openai_api_key` inputs and the `permissions: { pull-requests: write }`
  requirement. Documented as a breaking change with a before/after in the
  README.
- **Non-breaking for server/webhook/local-CLI:** the webhook and manual
  `/review` paths are unchanged; `superseded review` defaults to its current
  subprocess behavior. The containerized `api` server keeps working for the
  non-sandbox path (`SUPERSEDED_SANDBOX=0`).
- **One de-risking spike first:** confirm `sbx exec` stdin/stdout/readiness
  semantics; pick the `sandbox_io_mode` default accordingly, with the `sbx cp`
  fallback (`io_mode="cp"`) implemented regardless.

## Open questions to resolve during implementation

- Exact `sbx create` readiness signal and `sbx exec` stdin/stdout/exit-code
  behavior (resolved by the spike; determines `io_mode` default and whether a
  poll loop is needed after `sbx create`).
- Whether `sbx secret set` can be performed non-interactively for headless
  server provisioning (pipe the value), or whether an alternative credential
  path is needed for the host deployment.
