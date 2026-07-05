# smolvm Sandbox Executor — Design

**Date:** 2026-07-05
**Status:** Approved (pending user review)
**Related:** `2026-07-01-sandbox-executor-and-server-action-design.md`

## Problem

Server-mode reviews run each agent pass inside an `sbx` Docker Sandbox
microVM (`SandboxExecutor`, shelling out to the `sbx` CLI). `sbx` is
Linux/KVM-only, pre-installs the agent CLI via a built-in map, and
injects provider keys via a host keychain proxy (`sbx secret set -g`).

Operators on **macOS (Hypervisor.framework)** or **Windows (WHP)** hosts
cannot run `sbx`; their only sandbox option is unavailable. Provider-key
injection via the host keychain is also awkward for headless server
provisioning (the original sandbox spec lists non-interactive `sbx secret set`
as an open question).

We add **`smolvm`** as an alternative sandbox backend: a portable,
self-contained microVM runtime (<https://github.com/smol-machines/smolvm>)
that boots OCI images on macOS/Linux/Windows, supports bind-mounting the
repo checkout into the guest, and exposes an embeddable **Python SDK**
(`smolmachines` on PyPI, imported as `smol`) that runs the VMM **in-process**
(no CLI on PATH, no daemon). Secret injection is direct `ExecOptions.env`
per call — keys flow from the server's process environment into the guest
process env at exec time and never persist.

## Goals / Non-goals

**Goals**

- Add a `SmolvmExecutor` class implementing the existing
  `AgentExecutor`/`Session` Protocols, alongside `SandboxExecutor` —
  the executor abstraction is unchanged.
- Select server-side via `SandboxSettings.kind: str` (`"sbx"` default,
  `"smolvm"` opt-in) and the existing `SUPERSEDED_SANDBOX_*` env
  convention. Local CLI path (`--sandbox`) stays sbx-only (out of scope
  for this spec).
- One `smolvm` machine per review job; the 5 concurrent passes run as
  `m.exec(...)` calls against that single machine (mirrors the sbx
  invariant).
- Per-agent OCI images (operator-supplied; documented contract), each
  containing the agent CLI on PATH. A host-wide override lets ops bake
  all three agents into one image.
- Provider keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) injected per
  `exec` call via `ExecOptions.env` — no host keychain proxy, no
  persistent secret store, no Smolfile `[secrets]` referencing host env.
  Parity with the user-chosen "env per exec call" model.
- No change to agent prompts, `Agent.build_command()` /
  `parse_output()`, the merger, context gathering, or the worker's
  GitHub/store logic — the sandbox is purely a swap-in execution
  substrate (same invariant as the original sandbox spec).

**Non-goals**

- Exposing smolvm on the local CLI (`--sandbox smolvm`); the local
  `--sandbox` toggle keeps meaning sbx.
- A warm machine pool, per-pass machines, or smolvm-in-container (KVM
  passthrough into the `api` container).
- Switching the server default from `sbx` to `smolvm`. smolvm is an
  **alternative**, opted into per deployment.
- Shipping pre-built OCI agent images. Operators build/push their own;
  this repo only documents the contract.
- smolfleet cloud (`ConnectOptions(target="cloud")`). Local embedded
  engine only — parity with `sbx` being a host-level tool.

## Constraints discovered

- **`smolmachines` ships a native cp314 wheel.** Spiked
  `uv add --optional sandbox smolmachines` + `uv sync --extra sandbox`
  against `requires-python >=3.14` (this repo's pin): installs
  `smolmachines==1.4.5`, a 47.7 MiB wheel, no source build. Import works
  under Python 3.14.3.
- **`Machine.exec` has no stdin.** `ExecOptions` exposes only
  `env`/`workdir`/`timeout` — confirmed by inspection of the installed
  package. `Machine.write_file(path, data, mode)` exists, so prompt
  delivery follows the existing sbx `io_mode="cp"` pattern
  (`review/executor.py:181-209`): write the prompt to a guest temp
  file, then `< file` redirect in a shell. All three agents read prompt
  on stdin, so a single redirect is universal.
- **Alpine ships no `bash`** — `m.exec(["bash","-c", ...])` against
  `alpine:3.20` raises `SmolError` ("failed to spawn command"). Use
  `/bin/sh`, or bake `bash` into per-agent images. Per-agent images
  should declare `bash` anyway (agent CLIs are frequently bash-centric);
  the executor uses `sh` to be safe across base images.
- **`MountSpec(source, target, read_only=False)`**. Bind-mounts are
  writable by default (parity with `smolvm -v host:/workspace`). Mount
  target `/workspace` matches the smolvm workspace convention and
  receives the per-job repo checkout.
- **`ExecOptions.env` is per-call.** Spiked: `m.exec(["sh","-c","echo
  KEY=$TEST_KEY"], ExecOptions(env={"TEST_KEY":"secret-value-12345"}))`
  returns `KEY=secret-value-12345`. Keys never appear in argv of any
  other process, never persist on the machine record (the smolvm docs
  confirm secrets resolved at `exec` time are not stored).
- **`Machine.create` returns a running machine** in ~300ms on a KVM host
  (spiked). No separate `start` step; `state()` returns `"running"`
  immediately after `create`.
- **Embedded engine is in-process.** No `smolvm` CLI on PATH, no
  `smolvm serve` daemon — `Machine` drives libkrun directly from the
  Python process. Operator requirements are limited to the hypervisor
  (`/dev/kvm` on Linux, Hypervisor.framework on macOS, WHP on Windows)
  and the `smolmachines` pip extra.
- **Per-agent images are OCI** — `image = "registry/path/name[:tag]"`
  (or a `docker save` archive / unpacked rootfs; we use the registry
  form by default, documented as also accepting local archive paths).
  smolvm pulls via `crane`; the image just needs the agent CLI on
  `PATH` (and `sh`/`bash`).

## Design

### Architecture & data flow

```
PR event
  └─ GitHub Action (composite) — POST /review/pr (unchanged)
        │
        ▼
  Superseded server (FastAPI, hypervisor-capable host with optional
                     smolmachines extra installed)
    · resolve installation, fetch PR head/base, enqueue ReviewJob
             │
             ▼
  ReviewWorker._run_review_for_job  (sandbox-enabled branch)
    · checkout_repo → tmp_dir (existing)
    · resolve sandbox: SandboxSettings(kind="smolvm", smolvm_image_*, ...)
    · make_sandbox_executor(kind="smolvm", agent_name=config.agent,
                            ..., resolved_image=IMAGE) → SmolvmExecutor
    · executor.available(engine.agent) — ImportError → loud failure
    · engine.review(..., executor=SmolvmExecutor)
         ┌─ SmolvmSession ─────────────────────────────────────────┐
         │ m = Machine.create(MachineConfig(                       │
         │     name=f"superseded-{job_id}",                         │
         │     image=IMAGE,                                         │
         │     mounts=[MountSpec(checkout, "/workspace",            │
         │                        read_only=False)],                │
         │     resources=ResourceSpec(network=True)))               │
         │ for each pass (concurrent ThreadPoolExecutor):           │
         │   m.write_file("/tmp/_prompt_<rnd>.txt", prompt)         │
         │   m.exec(["sh","-c", "cd /workspace && <argv> < prompt"],  │
         │           ExecOptions(env=PROVIDER_KEYS,                 │
         │                        workdir="/workspace",             │
         │                        timeout=timeout))                 │
         │   → ExecResult.stdout; parse via agent.parse_output       │
         │ m.delete()                                                │
         └──────────────────────────────────────────────────────────┘
    · merge findings (unchanged merger)
    · post review + update check run (GitHub App token)
    · record findings/watermark to store
```

**Local-CLI path:** `superseded review` builds a `SubprocessExecutor`
(unchanged) or `SandboxExecutor` (sbx only). smolvm is not exposed on
the local CLI.

**Key invariant:** agent prompts, `Agent.build_command()`, `parse_output()`,
the merger, and the worker's GitHub/store logic are all untouched. The
smolvm sandbox is purely a swap-in execution substrate behind the
existing executor interface.

### Component map

| Status | File | Change |
|---|---|---|
| New | `src/superseded/review/executor.py` | `SmolvmExecutor` + `_SmolvmSession` classes; `make_sandbox_executor(kind=...)` gains dispatch |
| Mod | `src/superseded/server/worker.py` | `SandboxSettings` gains `kind`, `smolvm_binary` (kept for completeness), `smolvm_image`, `smolvm_image_<agent>`; `_run_review_for_job` resolves image + passes new kwargs |
| Mod | `src/superseded/server/config.py` | `ServerConfig` gains `sandbox_kind`, `smolvm_binary`, `smolvm_image_*`; `from_env()` extended |
| Mod | `pyproject.toml` | add `[project.optional-dependencies] sandbox = ["smolmachines"]` |
| Mod | `compose.yml` | add `SUPERSEDED_SANDBOX_KIND` env (default `sbx`) |
| Mod | `AGENTS.md` | add `smolmachines`/`smolvm` SDK runtime dep paragraph alongside `sbx` |
| Mod | README (if it lists sandbox ops) | host-deployment smolvm section, env vars, image build contract |

### The executor (`review/executor.py`)

A `Session` runs one agent command (prompt on stdin → JSON stdout); an
`AgentExecutor` opens a session rooted at a working directory. The
existing engine opens **one session per review** and the existing
`ThreadPoolExecutor` fans the 5 passes out as concurrent `session.run()`
calls against it. `SmolvmExecutor` slots into this unchanged.

```python
# New in executor.py
import importlib.util

SMOLVM_AVAILABLE = importlib.util.find_spec("smol") is not None

def _smol():
    """Import smol lazily so the module loads even when the optional
       smolmachines extra isn't installed."""
    try:
        from smol import ExecOptions, Machine, MachineConfig, MountSpec, ResourceSpec
    except ImportError as err:
        raise AgentRunError(
            "smolmachines extra not installed; run "
            "`uv sync --extra sandbox` to enable smolvm sandbox mode."
        ) from err
    return Machine, MachineConfig, MountSpec, ResourceSpec, ExecOptions


class SmolvmExecutor:
    """Runs agent CLIs inside a smolvm microVM via the embedded Python SDK.

    One Machine per session; provider keys injected per exec via
    ExecOptions.env (resolved from the server's own process environment).
    """

    def __init__(
        self,
        *,
        agent_name: str,
        image: str,
        name: str,
        cwd: str | Path | None = None,
        timeout: int = DEFAULT_SANDBOX_TIMEOUT,
        keep_on_error: bool = False,
        provider_keys_mapping: dict[str, str] | None = None,
    ) -> None:
        self._agent_name = agent_name
        self._image = image
        self._name = name
        self._cwd = cwd
        self._timeout = timeout
        self._keep_on_error = keep_on_error
        self._keys = provider_keys_mapping or _DEFAULT_PROVIDER_KEYS

    def available(self, agent: Agent) -> bool:
        return SMOLVM_AVAILABLE and self._image != ""

    def session(
        self, cwd: str | Path | None = None, *, env: dict[str, str] | None = None
    ) -> Session:
        resolved = cwd if cwd is not None else self._cwd
        if resolved is None:
            raise ValueError(
                "SmolvmExecutor requires a cwd (the repo checkout) for the "
                "machine workspace mount."
            )
        if env is not None and any(k in env for k in self._keys.values()):
            # honored per-exec via the server's host env, not via session env
            pass
        return _SmolvmSession(
            image=self._image,
            name=self._name,
            cwd=str(resolved),
            timeout=self._timeout,
            keep_on_error=self._keep_on_error,
            keys=_filter_provider_keys(self._keys, os.environ),
        )


class _SmolvmSession:
    """One smolvm machine, shared across the concurrent passes of a review.

    ``run()`` writes the prompt to a per-call guest file then exec's the
    agent argv with stdin redirected from that file. Per-pass invocations
    are independent Python calls, safe to run concurrently against the
    same Machine (passes are read-only reads of /workspace; the prompt
    file path is per-call UUID-named, no collision).
    """

    def __init__(
        self,
        *,
        image: str,
        name: str,
        cwd: str,
        timeout: int,
        keep_on_error: bool,
        keys: dict[str, str],
    ) -> None:
        self._image = image
        self._name = name
        self._cwd = cwd
        self._timeout = timeout
        self._keep_on_error = keep_on_error
        self._keys = keys
        self._machine = None  # type: ignore[assignment]
        self._errored = False

    def __enter__(self) -> _SmolvmSession:
        Machine, MachineConfig, MountSpec, ResourceSpec, _ = _smol()
        try:
            self._machine = Machine.create(
                MachineConfig(
                    name=self._name,
                    image=self._image,
                    mounts=[MountSpec(source=self._cwd, target="/workspace",
                                      read_only=False)],
                    resources=ResourceSpec(network=True),
                )
            )
        except Exception as err:
            raise AgentRunError(f"smol Machine.create failed: {err}") from err
        return self

    def __exit__(self, *exc: object) -> None:
        if self._keep_on_error and self._errored:
            logger.warning("keep_on_error: leaving smolvm %s for inspection",
                           self._name)
            return None
        try:
            if self._machine is not None:
                self._machine.delete()
        except Exception:
            logger.warning("smol delete failed for machine %s", self._name)
        return None

    def run(self, cmd: list[str], prompt: str, *, timeout: int) -> str:
        _, _, _, _, ExecOptions = _smol()
        prompt_rel = f"_smol_prompt_{uuid.uuid4().hex[:8]}.txt"
        prompt_guest = f"/tmp/{prompt_rel}"
        shell = f"cd /workspace && {shlex.join(cmd)} < {shlex.quote(prompt_guest)}"
        try:
            self._machine.write_file(prompt_guest, prompt)
            result = self._machine.exec(
                ["sh", "-c", shell],
                ExecOptions(env=dict(self._keys),
                            workdir="/workspace",
                            timeout=timeout),
            )
        except Exception as err:
            self._errored = True
            raise AgentRunError(f"smol exec failed: {err}") from err
        if result.exit_code != 0:
            self._errored = True
            stderr = result.stderr.strip()
            raise AgentRunError(
                f"Agent '{cmd[0]}' exited {result.exit_code}"
                + (f": {stderr}" if stderr else "")
            )
        return result.stdout
```

**`_DEFAULT_PROVIDER_KEYS`** is a single guest-env-name → host-env-name
map (both equal in practice), used for **all three agents**:

```python
_DEFAULT_PROVIDER_KEYS: dict[str, str] = {
    "ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY": "OPENAI_API_KEY",
}
```

`SmolvmExecutor.session()` filters this against the live `os.environ` —
only keys actually set on the server's host env are forwarded into the
guest (so claude-code gets only ANTHROPIC if OPENAI is unset, codex only
OPENAI, opencode both when both are present; absent host vars are
silently omitted, never raise). Servers commonly set both envs globally;
harmless presence of the wrong provider's key in an unrelated agent's
guest environment is fine (the agent only reads its own provider's
var). Operator override is via a future `provider_keys_mapping` arg
only — out of scope for v1.

```python
def _filter_provider_keys(
    mapping: dict[str, str], environ: dict[str, str]
) -> dict[str, str]:
    """Return only {guest_env: host_value} for host env vars actually set."""
    return {guest: environ[host] for guest, host in mapping.items()
            if host in environ}
```

**`make_sandbox_executor`** grows `kind` + smolvm kwargs; the sbx path is
unchanged:

```python
def make_sandbox_executor(
    *,
    kind: str = "sbx",
    agent_name: str,
    name: str,
    cwd: str | Path | None = None,
    timeout: int = DEFAULT_SANDBOX_TIMEOUT,
    keep_on_error: bool = False,
    binary: str = "sbx",        # sbx
    io_mode: str = "exec",      # sbx
    smolvm_binary: str = "smolvm",  # unused by SDK; kept for messages
    resolved_image: str | None = None,  # smolvm
) -> SandboxExecutor | SmolvmExecutor:
    if kind == "sbx":
        sbx_agent = SBX_AGENT_MAP.get(agent_name, agent_name)
        return SandboxExecutor(
            binary=binary, agent_name=sbx_agent, name=name, cwd=cwd,
            timeout=timeout, keep_on_error=keep_on_error, io_mode=io_mode,
        )
    if kind == "smolvm":
        if not resolved_image:
            raise ValueError(
                "smolvm executor requires resolved_image "
                "(set SUPERSEDED_SMOLVM_IMAGE or the per-agent "
                "SUPERSEDED_SMOLVM_IMAGE_<AGENT> env)."
            )
        return SmolvmExecutor(
            agent_name=agent_name, image=resolved_image, name=name, cwd=cwd,
            timeout=timeout, keep_on_error=keep_on_error,
        )
    raise ValueError(f"unknown sandbox kind: {kind!r}")
```

### Engine integration

**None.** `engine.review(...)` already takes an `executor: AgentExecutor
| None = None` (defaults to `SubprocessExecutor`) and calls `sess.run()`
once per pass. The `SmolvmExecutor` plugs in identically to
`SandboxExecutor`. Per-pass failure remains **skipped + warned, not
fatal** (today's behavior).

### Server-side changes

**`SandboxSettings` (server/worker.py):**

```python
@dataclass
class SandboxSettings:
    enabled: bool = False
    kind: str = "sbx"              # NEW: "sbx" | "smolvm"
    binary: str = "sbx"            # sbx (unchanged)
    timeout: int = 600
    keep_on_error: bool = False
    io_mode: str = "exec"          # sbx only
    smolvm_binary: str = "smolvm"  # NEW (unused by SDK; kept for messages)
    smolvm_image: str | None = None                         # NEW host-wide
    smolvm_image_claude: str | None = None                   # NEW
    smolvm_image_opencode: str | None = None                 # NEW
    smolvm_image_codex: str | None = None                    # NEW
```

**`ServerConfig` (server/config.py) additions** (env-overridable, matching
existing `SUPERSEDED_*` convention):

| Field | Default | Env |
|---|---|---|
| `sandbox_kind: str` | `"sbx"` | `SUPERSEDED_SANDBOX_KIND` |
| `smolvm_binary: str` | `"smolvm"` | `SUPERSEDED_SMOLVM_BINARY` |
| `smolvm_image: str \| None` | `None` | `SUPERSEDED_SMOLVM_IMAGE` |
| `smolvm_image_claude: str \| None` | `None` | `SUPERSEDED_SMOLVM_IMAGE_CLAUDE` |
| `smolvm_image_opencode: str \| None` | `None` | `SUPERSEDED_SMOLVM_IMAGE_OPENCODE` |
| `smolvm_image_codex: str \| None` | `None` | `SUPERSEDED_SMOLVM_IMAGE_CODEX` |

`from_env()` extended accordingly. The existing `sbx_binary` /
`sandbox_timeout` / `sandbox_keep_on_error` / `sandbox_io_mode` stay
sbx-specific; smolvm simply ignores `io_mode` (`/tmp` write_file path is
v1's only mode; if a future SDK release adds native stdin, we can read it
through the same `io_mode` field).

**Worker dispatch (`_run_review_for_job`):** localized change at the
existing executor-selection block (worker.py:452-468):

```python
executor = None
if sandbox is not None and sandbox.enabled:
    from superseded.review.executor import make_sandbox_executor
    if sandbox.kind == "smolvm":
        resolved_image = (sandbox.smolvm_image
                          or _agent_smolvm_image(sandbox, config.agent))
        if not resolved_image:
            raise RuntimeError(
                f"smolvm sandbox selected for agent {config.agent!r} but no "
                f"image configured (set SUPERSEDED_SMOLVM_IMAGE or "
                f"SUPERSEDED_SMOLVM_IMAGE_{config.agent.upper().replace('-','_')})."
            )
    executor = make_sandbox_executor(
        kind=sandbox.kind,
        agent_name=config.agent, name=f"superseded-{job.job_id}",
        timeout=sandbox.timeout, keep_on_error=sandbox.keep_on_error,
        binary=sandbox.binary, io_mode=sandbox.io_mode,            # sbx
        smolvm_binary=sandbox.smolvm_binary,                       # smolvm
        resolved_image=resolved_image if sandbox.kind == "smolvm" else None,
    )
    if not executor.available(engine.agent):
        raise RuntimeError(_sandbox_unavailable_msg(sandbox))
```

`_agent_smolvm_image(sandbox, agent)` returns
`sandbox.smolvm_image_claude`/`_opencode`/`_codex` per a small map keyed
off `config.agent`; claude-code → claude, opencode → opencode, codex →
codex.

`_sandbox_unavailable_msg(sandbox)` is a small helper factoring the
existing sbx message; for smolvm it points at
`SUPERSEDED_SMOLVM_IMAGE*` envs and `uv sync --extra sandbox`. **No silent
fallback** — parity with the existing sbx policy.

**CLI `serve`**: build `SandboxSettings` from `ServerConfig` (cli.py:801-
837), threading the new `kind`/`smolvm_*` fields. One-line additions.

### Config, packaging & operational requirements

**`pyproject.toml`:** add the optional dependency:

```toml
[project.optional-dependencies]
sandbox = ["smolmachines"]
```

Operators install via `uv sync --extra sandbox` (or
`pip install 'superseded[sandbox]'`). The `smolmachines` import is
guarded (`SMOLVM_AVAILABLE`), so the package still imports cleanly
without the extra — `SmolvmExecutor.available()` returns `False` and the
worker's `executor.available()` check fails loudly.

**Local config:** unchanged. `Config.sandbox: bool = False` in
`config.py`, `--sandbox`/`--no-sandbox` flag on `superseded review`, all
continue to mean sbx only (sbx has its own `SandboxExecutor` selection).
smolvm is server-only.

**No other Python dependencies.** The Agent abstraction, the engine, the
merger, and context gathering are untouched.

**Three documented deployment shapes:**

1. **Host deployment with sbx (the existing default for the Action
   target).** `superseded serve` on a KVM-capable Linux host where `sbx`
   lives; `SUPERSEDED_SANDBOX_KIND=sbx` (default). Unchanged from today.
2. **Host deployment with smolvm (the new, cross-platform alternative).**
   `superseded serve` on a macOS/Linux/Windows host with the
   hypervisor available; install the extra (`uv sync --extra sandbox`),
   set `SUPERSEDED_SANDBOX=1` + `SUPERSEDED_SANDBOX_KIND=smolvm` + the
   per-agent image env(s). Operator builds/pushes OCI images that contain
   the chosen agent CLI on PATH (and `sh`/`bash`).
3. **Container deployment (`compose.yml` — non-sandbox).** Unchanged: the
   `api` container keeps working for webhook/manual reviews via the
   in-process `SubprocessExecutor`. `compose.yml` gains the
   `SUPERSEDED_SANDBOX_KIND` env line (default `sbx`); smolvm-in-container
   is possible only with privileged + `/dev/kvm` + the sandbox extra —
   documented as unsupported/advanced.

**Operational requirements for the smolvm host (README + this spec):**

- macOS 11+/Apple Silicon (Hypervisor.framework), Linux x86_64/aarch64
  with `/dev/kvm` access, or Windows with WHP — per `smolmachines` docs.
- `uv sync --extra sandbox` (or the equivalent extra install) on the
  server.
- Per-agent OCI images built/pushed by the operator, set via
  `SUPERSEDED_SMOLVM_IMAGE_<AGENT>` (or `SUPERSEDED_SMOLVM_IMAGE`
  host-wide). Image contract: contains the agent CLI on PATH, plus
  `sh`/`bash` and any agent runtime deps (e.g. `python3` for opencode if
  it requires it).
- Server env: existing `SUPERSEDED_APP_ID` / `WEBHOOK_SECRET` /
  `PRIVATE_KEY_PATH` / `API_KEY` / `DATABASE_URL` / TLS-or-behind-proxy,
  plus `SUPERSEDED_SANDBOX=1`, `SUPERSEDED_SANDBOX_KIND=smolvm`,
  `SUPERSEDED_SANDBOX_TIMEOUT` / `_KEEP_ON_ERROR` as desired, and the
  model-provider API keys (`ANTHROPIC_API_KEY` /
  `OPENAI_API_KEY`) — the server's own process env, which is read per
  exec and forwarded into the guest. Never use `smolvm serve` HTTP API
  for this path (the SDK's `from_env` secret refs would let the server
  read its own env; that's fine, but exec-env is the simpler flow and
  uses no Smolfile secret mechanism).

**Provider-key edge cases:**
- Host env missing the key an agent needs → the agent fails its auth
  call with the standard provider error surfaced via `AgentRunError`,
  per pass (skipped + warned, not fatal). Same outcome as running the
  agent CLI unauthenticated on the host.
- Operator running claude-code + opencode in the same review job — if
  only `ANTHROPIC_API_KEY` is set, both passes get it; if codex is also
  a configured pass, codex's pass fails auth. Operator controls which
  passes run (`config.passes`) and which keys exist on the server.

### Testing strategy

All tests mock the smol SDK and the host environment — **no live
microVM, `gh`, or AI-CLI calls** (per AGENTS.md; smolvm joins that list).
pytest with `asyncio_mode = "auto"`.

**`tests/test_executor.py` (modify — already exists for sbx):**
- `SmolvmExecutor`: mock `smol` import (or use a fake submodule via
  `importlib` shim inserted into `sys.modules`); assert
  - `available()` returns True iff `find_spec("smol") is not None` AND
    `resolved_image != ""`.
  - `session().__enter__` calls `Machine.create(MachineConfig(name=...,
    image=..., mounts=[MountSpec(cwd, "/workspace", read_only=False)],
    resources=ResourceSpec(network=True)))` once; non-empty name pattern
    `superseded-<id>`.
  - `run(cmd, prompt, timeout)` calls `write_file("/tmp/_smol_prompt_*.txt",
    prompt)` then `exec(["sh","-c", "cd /workspace && <argv> < <file>"],
    ExecOptions(env=..., workdir="/workspace", timeout=timeout))`; returns
    `stdout`; non-zero exit → `AgentRunError` (exit-code/stderr message).
  - `__exit__` always deletes the machine; skipped on error only when
    `keep_on_error`.
  - `Machine.create` raising → `AgentRunError`; `m.exec` raising →
    `AgentRunError` with `_errored=True`.
  - `_DEFAULT_PROVIDER_KEYS`: only keys present in `os.environ` get
    forwarded (a server env with neither key set → empty dict, agent
    auth fails downstream — verify no KeyError).
- `make_sandbox_executor(kind=...)`: `kind="sbx"` returns
  `SandboxExecutor`; `kind="smolvm"` returns `SmolvmExecutor`;
  `kind="other"` raises `ValueError`; `kind="smolvm"` without
  `resolved_image` raises `ValueError` with the env-name hint.

**`tests/test_worker.py` (modify):** smolvm dispatch — `sandbox.kind="smolvm"` +
`smolvm_image_claude` set + `config.agent="claude-code"` →
`SmolvmExecutor` built with `image=smolvm_image_claude`; sandbox.kind="smolvm"
but image unset → job fails with check-run "failure" / "set
SUPERSEDED_SMOLVM_IMAGE*" message (no silent fallback). `sandbox.kind="sbx"`
→ existing sbx executor, unchanged.

**`tests/test_server_config.py` (modify):** `from_env()` parses
`SUPERSEDED_SANDBOX_KIND`, `_SMOLVM_BINARY`, `_SMOLVM_IMAGE`, and the
three per-agent image envs; defaults are `"sbx"` / `"smolvm"` / None /
None / None / None.

**`tests/test_cli.py` (modify — `serve` path only):** the `serve`
command threads the new `SandboxSettings` fields from `ServerConfig`
(small assertion test that the data is passed through; the heavier
behavioral tests live in `test_worker`/`test_executor`).

**End-to-end smoke (optional, marked `@pytest.mark.kvm` and skipped by
default — mirrors the existing `postgres` marker pattern):** with
`SMOLVM_E2E=1` + `smolmachines` installed + the repo's alpine:3.20 image
+ a dummy agent CLI baked in, run one trivial review pass against a real
`Machine` on a KVM host and assert that the prompt bytes arrive. Not run
in default `uv run pytest`; documents the SDK contract for future
regressions. (Optional in v1; can defer if it would balloon the change.)

### Image-build contract (documentation only — no images ship)

For each agent, the operator builds an OCI image whose `PATH` contains
the named agent CLI. Examples (operators choose their own base):

```dockerfile
# claude-code.smolimage.Dockerfile (operator-owned)
FROM alpine:3.20
RUN apk add --no-cache bash nodejs npm
RUN npm install -g @anthropic-ai/claude-code
# verify:
RUN claude --version
```

The README documents:
- A "must contain the agent CLI on PATH" invariant.
- That the executor invokes `m.exec(agent_argv, ...)` where `agent_argv`
  is whatever `Agent.build_command()` produces today (no path munging).
- Image must include `sh` (Alpine does by default; preferred base images
  do too).
- Network is open (`ResourceSpec(network=True)`) — agents need egress to
  their provider.
- Examples for claude-code, opencode, codex (pointers, not authored
  here).

## Migration / backward compatibility

- **Non-breaking for everyone.** Default `sandbox_kind="sbx"` preserves
  the existing behavior. Existing sbx host deployments need no change.
  Existing compose deployments (sandbox off) are untouched.
- **Additive only on the opt-in path.** Operators who set
  `SUPERSEDED_SANDBOX_KIND=smolvm` install the extra + supply images;
  nobody else is affected. The local CLI and the GitHub Action change
  nothing.
- **`smolmachines` import is fully optional.** Without the extra, the
  `from smol import ...` is never executed (`SmolvmExecutor.available()`
  returns `False` before any import attempt); the module imports cleanly
  under the existing test suite.

## Open questions to resolve during implementation

- **Native stdin via a future SDK version.** If `smolmachines` adds an
  `ExecOptions.stdin` (or `input`) field in a later release, the
  `write_file + < file` workaround collapses to a one-line `input=prompt`
  and the prompt-file path is dropped. Track via a comment in
  `_SmolvmSession.run` and revisit when bumping the optional-dep pin.
- **Whether to support a single combined image (all three agents baked
  in) for ops convenience.** Today's `SUPERSEDED_SMOLVM_IMAGE` host-wide
  override already covers this; should we also fall back to it
  transparently when a per-agent image is unset, rather than failing
  loudly? v1 fails loudly — operators using one image must set
  `SUPERSEDED_SMOLVM_IMAGE` explicitly. Revisit if the cross-CLI
  deployment shape becomes common.
- **`ResourceSpec` memory defaults.** SDK default is 8192 MiB; the
  smolvm CLI default is also 8192 MiB but `Machine.create` accepts
  `resources.memory_mb=None` → SDK default. v1 leaves it unspecified
  (elastic via virtio balloon, the docs say). If memory pressure on the
  host becomes a concern, expose `SUPERSEDED_SMOLVM_MEMORY_MB` /
  `_CPUS` envs.
- **Machine name uniqueness across hosts.** `superseded-<job_id>`
  (job_id is `uuid.uuid4().hex[:12]`) is unique per review job per host.
  Multi-host deployments would need a host suffix; out of scope for v1
  (one server instance per host, parity with sbx).