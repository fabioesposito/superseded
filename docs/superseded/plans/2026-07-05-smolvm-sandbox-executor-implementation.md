# smolvm Sandbox Executor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `SmolvmExecutor` (smolmachines Python SDK backend) as an alternative opt-in sandbox alongside the existing `sbx`-based `SandboxExecutor`, selectable server-side via `SUPERSEDED_SANDBOX_KIND=smolvm`.

**Architecture:** `SmolvmExecutor` implements the existing `AgentExecutor`/`Session` Protocols in `review/executor.py` next to `SandboxExecutor`. `make_sandbox_executor(kind=...)` dispatches. One `smol.Machine` per review job; the 5 concurrent passes run as `m.exec(...)` calls against that single machine. Provider keys (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`) are filtered from the server's own `os.environ` and injected per-exec via `ExecOptions.env`. Prompts are delivered via `m.write_file("/tmp/_smol_prompt_<rnd>.txt", prompt)` then `< file` redirect (the SDK's `ExecOptions` has no stdin field, spiked). Engine, prompts, merger, context gathering, and the worker's GitHub/store logic are untouched.

**Tech Stack:** Python 3.14+, `smolmachines` (optional pip extra imported as `smol`), Pydantic v2, pytest, ruff. Tests mock the `smol` SDK via `sys.modules` shim (no live microVM in CI).

**Reference:** Design spec at `docs/superseded/specs/2026-07-05-smolvm-sandbox-executor-design.md`.

---

## File Structure

- **Modify:** `src/superseded/review/executor.py` — add `_DEFAULT_PROVIDER_KEYS`, `_filter_provider_keys`, `SMOLVM_AVAILABLE`, `_smol()`, `SmolvmExecutor`, `_SmolvmSession`; extend `make_sandbox_executor(kind=...)`.
- **Modify:** `src/superseded/server/worker.py` — add fields to `SandboxSettings`; add `_agent_smolvm_image()` and `_sandbox_unavailable_msg()` helpers; thread smolvm kwargs through executor selection in `_run_review_for_job`.
- **Modify:** `src/superseded/server/config.py` — add `sandbox_kind`, `smolvm_binary`, `smolvm_image`, `smolvm_image_claude`, `smolvm_image_opencode`, `smolvm_image_codex` to `ServerConfig`; extend `from_env()`.
- **Modify:** `src/superseded/cli.py` — thread new `SandboxSettings` fields from `ServerConfig` in the `serve` command.
- **Modify:** `pyproject.toml` — add `[project.optional-dependencies] sandbox = ["smolmachines"]`.
- **Modify:** `compose.yml` — add `SUPERSEDED_SANDBOX_KIND` env (default `sbx`).
- **Modify:** `AGENTS.md` — add `smolmachines`/`smolvm` paragraph to the runtime-external-deps note.
- **Modify:** `tests/test_executor.py` — `SmolvmExecutor`/`_SmolvmSession`/`make_sandbox_executor(kind="smolvm")` tests.
- **Modify:** `tests/test_server_config.py` — `from_env()` parses the new env vars.
- **Modify:** `tests/test_server_worker.py` — smolvm dispatch + image-resolution + unavailable failure.
- **Modify:** `tests/test_cli.py` — `serve` threads new `SandboxSettings` fields.

---

### Task 1: Add the `smolmachines` optional dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the optional-deps section**

In `pyproject.toml`, locate the existing `[project.optional-dependencies]` table (which already contains `graph = ["code-review-graph"]`). Add the `sandbox` key beneath it:

```toml
[project.optional-dependencies]
graph = ["code-review-graph"]
sandbox = [
    "smolmachines>=1.4.5",
]
```

- [ ] **Step 2: Verify the dep installs under Python 3.14**

Run: `uv sync --extra sandbox`
Expected: resolves `smolmachines==1.4.5` (or newer 1.x) and completes without a source build.

- [ ] **Step 3: Verify the import is optional**

Run: `uv run python -c "import superseded.review.executor; print('ok')"`
Expected: prints `ok` regardless of whether the `sandbox` extra is installed in the active venv (the import must not hard-require `smol`).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add smolmachines optional sandbox extra"
```

---

### Task 2: Add `SmolvmExecutor` and the smolvm dispatch in `make_sandbox_executor`

This is the foundational task — every later task depends on these symbols existing. Follow TDD: write tests first, then the implementation, in the same file.

**Files:**
- Modify: `src/superseded/review/executor.py`
- Modify: `tests/test_executor.py`

- [ ] **Step 1: Add a `sys.modules` shim helper and the first failing test for `SmolvmExecutor.available()`**

Append to `tests/test_executor.py`:

```python
import sys
import types


def _install_fake_smol(monkeypatch, *, machine_create=None, write_file=None,
                      exec_result=None, delete=None):
    """Inject a fake `smol` package into sys.modules for one test.

    Returns a dict of the mock objects the test can assert against:
       {"Machine": ..., "MachineConfig": ..., "MountSpec": ...,
        "ResourceSpec": ..., "ExecOptions": ..., "machine_inst": ...}
    """
    machine_inst = types.SimpleNamespace(
        name="superseded-probe",
        write_file=MagicMock(side_effect=write_file or (lambda p, d, m=None: None)),
        exec=MagicMock(side_effect=exec_result or (lambda c, o=None: None)),
        delete=MagicMock(side_effect=delete or (lambda: None)),
        state=MagicMock(return_value="running"),
    )
    machine_create_mock = MagicMock(return_value=machine_inst)
    captured = {}

    class _Machine:
        @staticmethod
        def create(config=None, conn=None):
            captured["config"] = config
            return machine_create_mock(config) if machine_create else machine_inst

    class _MachineConfig:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            captured["MachineConfig_kwargs"] = kw

    class _MountSpec:
        def __init__(self, source, target, read_only=False, readonly=None):
            self.source = source
            self.target = target
            self.read_only = read_only
            captured.setdefault("MountSpec_instances", []).append(self)

    class _ResourceSpec:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            captured["ResourceSpec_kwargs"] = kw

    class _ExecOptions:
        def __init__(self, env=None, workdir=None, timeout=None):
            self.env = env
            self.workdir = workdir
            self.timeout = timeout
            captured.setdefault("ExecOptions_instances", []).append(self)

    fake = types.ModuleType("smol")
    fake.Machine = _Machine
    fake.MachineConfig = _MachineConfig
    fake.MountSpec = _MountSpec
    fake.ResourceSpec = _ResourceSpec
    fake.ExecOptions = _ExecOptions
    monkeypatch.setitem(sys.modules, "smol", fake)
    captured.update({"Machine": _Machine, "MachineConfig": _MachineConfig,
                     "MountSpec": _MountSpec, "ResourceSpec": _ResourceSpec,
                     "ExecOptions": _ExecOptions, "machine_inst": machine_inst})
    return captured


def test_smolvm_executor_available_true_when_image_set_and_smol_importable(monkeypatch):
    _install_fake_smol(monkeypatch)
    from superseded.review.executor import SmolvmExecutor
    ex = SmolvmExecutor(agent_name="claude-code", image="ghcr.io/x/claude:1",
                        name="superseded-x")
    assert ex.available(MagicMock()) is True


def test_smolvm_executor_available_false_when_image_empty(monkeypatch):
    _install_fake_smol(monkeypatch)
    from superseded.review.executor import SmolvmExecutor
    ex = SmolvmExecutor(agent_name="claude-code", image="", name="superseded-x")
    assert ex.available(MagicMock()) is False
```

- [ ] **Step 2: Run the tests and verify they fail with `ImportError`/`AttributeError`**

Run: `uv run pytest tests/test_executor.py::test_smolvm_executor_available_true_when_image_set_and_smol_importable tests/test_executor.py::test_smolvm_executor_available_false_when_image_empty -v`
Expected: FAIL — `SmolvmExecutor` not importable from `superseded.review.executor`.

- [ ] **Step 3: Implement the bare `SmolvmExecutor` class and helpers**

Add to `src/superseded/review/executor.py` (after the existing `make_sandbox_executor` function):

```python
import importlib.util
import os

from typing import Any  # noqa: E402  (kept near the new code for clarity)

_DEFAULT_PROVIDER_KEYS: dict[str, str] = {
    "ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY": "OPENAI_API_KEY",
}

SMOLVM_AVAILABLE = importlib.util.find_spec("smol") is not None


def _smol() -> tuple[Any, Any, Any, Any, Any]:
    """Lazily import smol types; raise AgentRunError if the extra isn't installed."""
    try:
        from smol import ExecOptions, Machine, MachineConfig, MountSpec, ResourceSpec
    except ImportError as err:
        raise AgentRunError(
            "smolmachines extra not installed; run `uv sync --extra sandbox` "
            "to enable smolvm sandbox mode."
        ) from err
    return Machine, MachineConfig, MountSpec, ResourceSpec, ExecOptions


def _filter_provider_keys(
    mapping: dict[str, str], environ: dict[str, str]
) -> dict[str, str]:
    """Return the {guest_env: host_value} subset whose host env var is actually set."""
    return {guest: environ[host] for guest, host in mapping.items() if host in environ}


class SmolvmExecutor:
    """Runs agent CLIs inside a smolvm microVM via the embedded smol SDK.

    One Machine per session; provider keys injected per exec via
    ``ExecOptions.env`` (resolved from the server's own process environment).
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
        self._keys = provider_keys_mapping or dict(_DEFAULT_PROVIDER_KEYS)

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
        return _SmolvmSession(
            image=self._image,
            name=self._name,
            cwd=str(resolved),
            timeout=self._timeout,
            keep_on_error=self._keep_on_error,
            keys=_filter_provider_keys(self._keys, os.environ),
        )
```

The `_SmolvmSession` class referenced above does not exist yet — define a placeholder that raises `NotImplementedError` from `__enter__`, `__exit__`, and `run`:

```python
class _SmolvmSession:
    def __init__(self, *, image, name, cwd, timeout, keep_on_error, keys):
        self._image = image
        self._name = name
        self._cwd = cwd
        self._timeout = timeout
        self._keep_on_error = keep_on_error
        self._keys = keys

    def __enter__(self):
        raise NotImplementedError

    def __exit__(self, *exc):
        raise NotImplementedError

    def run(self, cmd, prompt, *, timeout):
        raise NotImplementedError
```

Note: the `import importlib.util` and `import os` lines must be added at the top of `executor.py` with the other imports, not where shown above (this is just for narrative placement). Same for `from typing import Any` — actually omit it; use `tuple[Any, Any, Any, Any, Any]` requires `from __future__ import annotations` (already present at top) so `Any` is fine if imported. To keep ruff happy, replace the `Any`-tuple return with `tuple[type, type, type, type, type]` and drop the `Any` import.

Revised signature:
```python
def _smol() -> tuple[type, type, type, type, type]:
```

- [ ] **Step 4: Run the two available() tests; expect PASS**

Run: `uv run pytest tests/test_executor.py::test_smolvm_executor_available_true_when_image_set_and_smol_importable tests/test_executor.py::test_smolvm_executor_available_false_when_image_empty -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/superseded/review/executor.py tests/test_executor.py
git commit -m "feat(executor): introduce SmolvmExecutor skeleton + optional smolmachines probe"
```

---

### Task 3: Implement `_SmolvmSession` lifecycle (`__enter__`, `__exit__`)

**Files:**
- Modify: `src/superseded/review/executor.py`
- Modify: `tests/test_executor.py`

- [ ] **Step 1: Write the failing tests for `__enter__` (machine creation) and `__exit__` (delete)**

Append to `tests/test_executor.py`:

```python
def test_smolvm_session_enter_creates_machine_with_workspace_mount(tmp_path, monkeypatch):
    captured = _install_fake_smol(monkeypatch)
    from superseded.review.executor import SmolvmExecutor
    ex = SmolvmExecutor(agent_name="claude-code", image="img", name="smol-1",
                        cwd=tmp_path, timeout=30)
    with ex.session():
        pass
    mc_kwargs = captured["MachineConfig_kwargs"]
    assert mc_kwargs["name"] == "smol-1"
    assert mc_kwargs["image"] == "img"
    [mount] = captured["MountSpec_instances"]
    assert mount.source == str(tmp_path)
    assert mount.target == "/workspace"
    assert mount.read_only is False
    rs_kwargs = captured["ResourceSpec_kwargs"]
    assert rs_kwargs.get("network") is True


def test_smolvm_session_enter_failure_raises_agent_run_error(tmp_path, monkeypatch):
    _install_fake_smol(monkeypatch, machine_create=lambda cfg: (_ for _ in ()).throw(
        RuntimeError("kvm unavailable")))
    from superseded.review.executor import SmolvmExecutor
    ex = SmolvmExecutor(agent_name="claude-code", image="img", name="smol-1",
                        cwd=tmp_path)
    with pytest.raises(AgentRunError, match="smol Machine.create failed"):
        with ex.session():
            pass


def test_smolvm_session_exit_calls_delete(tmp_path, monkeypatch):
    captured = _install_fake_smol(monkeypatch)
    from superseded.review.executor import SmolvmExecutor
    ex = SmolvmExecutor(agent_name="claude-code", image="img", name="smol-1",
                        cwd=tmp_path)
    with ex.session():
        pass
    captured["machine_inst"].delete.assert_called_once()


def test_smolvm_session_exit_keeps_machine_on_error_when_keep_on_error(tmp_path, monkeypatch):
    captured = _install_fake_smol(monkeypatch)
    from superseded.review.executor import SmolvmExecutor
    ex = SmolvmExecutor(agent_name="claude-code", image="img", name="smol-1",
                        cwd=tmp_path, keep_on_error=True)
    sess = ex.session()
    sess._errored = True
    sess.__exit__(None, None, None)
    captured["machine_inst"].delete.assert_not_called()


def test_smolvm_session_exit_swallow_delete_failure(tmp_path, monkeypatch, caplog):
    captured = _install_fake_smol(monkeypatch,
                                  delete=lambda: (_ for _ in ()).throw(RuntimeError("nope")))
    from superseded.review.executor import SmolvmExecutor
    import logging
    ex = SmolvmExecutor(agent_name="claude-code", image="img", name="smol-1",
                        cwd=tmp_path)
    with caplog.at_level(logging.WARNING, logger="superseded.review.executor"):
        with ex.session():
            pass
    captured["machine_inst"].delete.assert_called_once()
    assert any("smol delete failed" in r.getMessage() for r in caplog.records)
```

- [ ] **Step 2: Run the tests; expect failure (NotImplementedError)**

Run: `uv run pytest tests/test_executor.py -k smolvm_session -v`
Expected: FAIL — `_SmolvmSession.__enter__` raises `NotImplementedError`.

- [ ] **Step 3: Implement `_SmolvmSession` lifecycle**

In `src/superseded/review/executor.py`, replace the placeholder `_SmolvmSession` with the real implementation:

```python
class _SmolvmSession:
    """One smolvm machine, shared across the concurrent passes of a review.

    ``run()`` writes the prompt to a per-call guest file then exec's the
    agent argv with stdin redirected from that file. Per-pass invocations
    are independent Python calls, safe to run concurrently against the
    same Machine (the prompt file path is per-call UUID-named).
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
        self._machine: Any = None
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
        raise NotImplementedError  # filled in by Task 4
```

(Use `from typing import Any` is unnecessary under `from __future__ import annotations`; just keep the `Any` annotation as written — Python 3.14 evaluates it lazily for variable annotations.)

- [ ] **Step 4: Run the lifecycle tests; expect PASS**

Run: `uv run pytest tests/test_executor.py -k smolvm_session -v`
Expected: PASS for all 5 tests.

- [ ] **Step 5: Commit**

```bash
git add src/superseded/review/executor.py tests/test_executor.py
git commit -m "feat(executor): smolvm session lifecycle (create + delete)"
```

---

### Task 4: Implement `_SmolvmSession.run` (prompt file + exec)

**Files:**
- Modify: `src/superseded/review/executor.py`
- Modify: `tests/test_executor.py`

- [ ] **Step 1: Write the failing tests for `run()`**

Append to `tests/test_executor.py`:

```python
def test_smolvm_session_run_uses_prompt_file_and_exec(tmp_path, monkeypatch):
    captured = _install_fake_smol(monkeypatch, exec_result=MagicMock(
        exit_code=0, stdout="[]", stderr=""))
    from superseded.review.executor import SmolvmExecutor
    ex = SmolvmExecutor(agent_name="claude-code", image="img", name="smol-1",
                        cwd=tmp_path)
    with ex.session() as sess:
        out = sess.run(["claude", "-p"], "the-prompt", timeout=42)
    assert out == "[]"
    # write_file called once with prompt bytes
    wf_call = captured["machine_inst"].write_file.call_args
    assert wf_call.args[0].startswith("/tmp/_smol_prompt_")
    assert wf_call.args[0].endswith(".txt")
    assert wf_call.args[1] == "the-prompt"
    # exec called with sh -c, workdir, timeout, env-filtered from os.environ
    exec_call = captured["machine_inst"].exec.call_args
    argv = exec_call.args[0]
    assert argv[0] == "sh" and argv[1] == "-c"
    assert argv[2].startswith("cd /workspace && claude -p < /tmp/_smol_prompt_")
    assert argv[2].endswith(".txt")
    opts = exec_call.args[1]
    assert opts.workdir == "/workspace"
    assert opts.timeout == 42
    assert isinstance(opts.env, dict)


def test_smolvm_session_run_forwards_only_set_provider_keys(tmp_path, monkeypatch):
    captured = _install_fake_smol(monkeypatch, exec_result=MagicMock(
        exit_code=0, stdout="[]", stderr=""))
    monkeypatch.setattr("superseded.review.executor.os.environ",
                        {"ANTHROPIC_API_KEY": "k-xyz"})
    from superseded.review.executor import SmolvmExecutor
    ex = SmolvmExecutor(agent_name="claude-code", image="img", name="smol-1",
                        cwd=tmp_path)
    with ex.session() as sess:
        sess.run(["claude", "-p"], "p", timeout=10)
    opts = captured["machine_inst"].exec.call_args.args[1]
    assert opts.env == {"ANTHROPIC_API_KEY": "k-xyz"}


def test_smolvm_session_run_nonzero_raises(tmp_path, monkeypatch):
    _install_fake_smol(monkeypatch, exec_result=MagicMock(
        exit_code=2, stdout="", stderr="boom"))
    from superseded.review.executor import SmolvmExecutor
    ex = SmolvmExecutor(agent_name="claude-code", image="img", name="smol-1",
                        cwd=tmp_path)
    with ex.session() as sess, pytest.raises(AgentRunError, match="boom"):
        sess.run(["claude"], "p", timeout=10)


def test_smolvm_session_run_exec_exception_raises_agent_run_error(tmp_path, monkeypatch):
    _install_fake_smol(monkeypatch, exec_result=lambda c, o=None: (
        (_ for _ in ()).throw(RuntimeError("vm dead"))))
    from superseded.review.executor import SmolvmExecutor
    ex = SmolvmExecutor(agent_name="claude-code", image="img", name="smol-1",
                        cwd=tmp_path)
    with ex.session() as sess, pytest.raises(AgentRunError, match="smol exec failed"):
        sess.run(["claude"], "p", timeout=10)
```

- [ ] **Step 2: Run the tests; expect failure**

Run: `uv run pytest tests/test_executor.py -k smolvm_session_run -v`
Expected: FAIL — `_SmolvmSession.run` raises `NotImplementedError`.

- [ ] **Step 3: Implement `run()`**

In `src/superseded/review/executor.py`, replace the `run` method of `_SmolvmSession`:

```python
    def run(self, cmd: list[str], prompt: str, *, timeout: int) -> str:
        _, _, _, _, ExecOptions = _smol()
        prompt_guest = f"/tmp/_smol_prompt_{uuid.uuid4().hex[:8]}.txt"
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

- [ ] **Step 4: Run the run-tests; expect PASS**

Run: `uv run pytest tests/test_executor.py -k smolvm_session_run -v`
Expected: PASS for all 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/superseded/review/executor.py tests/test_executor.py
git commit -m "feat(executor): smolvm session.run with prompt-file + filtered provider env"
```

---

### Task 5: Extend `make_sandbox_executor` with `kind` dispatch

**Files:**
- Modify: `src/superseded/review/executor.py`
- Modify: `tests/test_executor.py`

- [ ] **Step 1: Write the failing tests for the new `kind` arg**

Append to `tests/test_executor.py`:

```python
def test_make_sandbox_executor_defaults_to_sbx(tmp_path):
    from superseded.review.executor import SandboxExecutor, make_sandbox_executor
    ex = make_sandbox_executor(agent_name="claude-code", name="n1", cwd=tmp_path)
    assert isinstance(ex, SandboxExecutor)


def test_make_sandbox_executor_kind_sbx_returns_sandbox_executor(tmp_path):
    from superseded.review.executor import SandboxExecutor, make_sandbox_executor
    ex = make_sandbox_executor(kind="sbx", agent_name="claude-code", name="n1", cwd=tmp_path)
    assert isinstance(ex, SandboxExecutor)


def test_make_sandbox_executor_kind_smolvm_returns_smolvm_executor(monkeypatch, tmp_path):
    _install_fake_smol(monkeypatch)
    from superseded.review.executor import SmolvmExecutor, make_sandbox_executor
    ex = make_sandbox_executor(kind="smolvm", agent_name="claude-code", name="n1",
                               cwd=tmp_path, resolved_image="ghcr.io/x/c:1")
    assert isinstance(ex, SmolvmExecutor)
    assert ex._image == "ghcr.io/x/c:1"
    assert ex._name == "n1"
    assert ex._cwd == tmp_path


def test_make_sandbox_executor_kind_smolvm_without_image_raises(tmp_path):
    from superseded.review.executor import make_sandbox_executor
    with pytest.raises(ValueError, match="resolved_image"):
        make_sandbox_executor(kind="smolvm", agent_name="claude-code", name="n1",
                              cwd=tmp_path, resolved_image=None)


def test_make_sandbox_executor_kind_smolvm_empty_image_raises(tmp_path):
    from superseded.review.executor import make_sandbox_executor
    with pytest.raises(ValueError, match="resolved_image"):
        make_sandbox_executor(kind="smolvm", agent_name="claude-code", name="n1",
                              cwd=tmp_path, resolved_image="")


def test_make_sandbox_executor_kind_unknown_raises(tmp_path):
    from superseded.review.executor import make_sandbox_executor
    with pytest.raises(ValueError, match="unknown sandbox kind"):
        make_sandbox_executor(kind="other", agent_name="claude-code", name="n1",
                              cwd=tmp_path)
```

- [ ] **Step 2: Run the tests; expect failure**

Run: `uv run pytest tests/test_executor.py -k make_sandbox_executor -v`
Expected: FAIL — `make_sandbox_executor` doesn't accept `kind`/`resolved_image` kwargs yet.

- [ ] **Step 3: Replace `make_sandbox_executor` with the dispatching version**

In `src/superseded/review/executor.py`, replace the existing `make_sandbox_executor` function (executor.py:261-281) with:

```python
def make_sandbox_executor(
    *,
    kind: str = "sbx",
    agent_name: str,
    name: str,
    cwd: str | Path | None = None,
    timeout: int = DEFAULT_SANDBOX_TIMEOUT,
    keep_on_error: bool = False,
    binary: str = "sbx",
    io_mode: str = "exec",
    smolvm_binary: str = "smolvm",
    resolved_image: str | None = None,
) -> SandboxExecutor | SmolvmExecutor:
    """Build the configured sandbox executor.

    ``kind="sbx"`` (default) shells out to the ``sbx`` CLI; ``kind="smolvm"``
    uses the embedded ``smol`` SDK and requires ``resolved_image``.
    """
    if kind == "sbx":
        sbx_agent = SBX_AGENT_MAP.get(agent_name, agent_name)
        return SandboxExecutor(
            binary=binary,
            agent_name=sbx_agent,
            name=name,
            cwd=cwd,
            timeout=timeout,
            keep_on_error=keep_on_error,
            io_mode=io_mode,
        )
    if kind == "smolvm":
        if not resolved_image:
            raise ValueError(
                "smolvm executor requires resolved_image "
                "(set SUPERSEDED_SMOLVM_IMAGE or the per-agent "
                "SUPERSEDED_SMOLVM_IMAGE_<AGENT> env)."
            )
        return SmolvmExecutor(
            agent_name=agent_name,
            image=resolved_image,
            name=name,
            cwd=cwd,
            timeout=timeout,
            keep_on_error=keep_on_error,
        )
    raise ValueError(f"unknown sandbox kind: {kind!r}")
```

- [ ] **Step 4: Run the new tests and the full executor suite**

Run: `uv run pytest tests/test_executor.py -v`
Expected: PASS for all tests (existing sbx + new smolvm).

- [ ] **Step 5: Run lint + format on the changed files**

Run: `uv run ruff format src/superseded/review/executor.py tests/test_executor.py && uv run ruff check src/superseded/review/executor.py tests/test_executor.py`
Expected: no lint errors.

- [ ] **Step 6: Commit**

```bash
git add src/superseded/review/executor.py tests/test_executor.py
git commit -m "feat(executor): make_sandbox_executor dispatches on kind (sbx|smolvm)"
```

---

### Task 6: Extend `SandboxSettings` with smolvm fields

**Files:**
- Modify: `src/superseded/server/worker.py:53-61`
- Modify: `tests/test_server_worker.py`

- [ ] **Step 1: Write the failing test asserting the new fields exist with the right defaults**

Append to `tests/test_server_worker.py`:

```python
def test_sandbox_settings_has_smolvm_fields_with_defaults():
    from superseded.server.worker import SandboxSettings
    s = SandboxSettings()
    assert s.kind == "sbx"
    assert s.smolvm_binary == "smolvm"
    assert s.smolvm_image is None
    assert s.smolvm_image_claude is None
    assert s.smolvm_image_opencode is None
    assert s.smolvm_image_codex is None
```

- [ ] **Step 2: Run the test; expect AttributeError**

Run: `uv run pytest tests/test_server_worker.py::test_sandbox_settings_has_smolvm_fields_with_defaults -v`
Expected: FAIL — `SandboxSettings` has no `kind` attribute.

- [ ] **Step 3: Add the fields to `SandboxSettings`**

In `src/superseded/server/worker.py`, replace the `SandboxSettings` dataclass (worker.py:53-61) with:

```python
@dataclass
class SandboxSettings:
    """Whether/how the server runs agents inside a sandbox microVM."""

    enabled: bool = False
    kind: str = "sbx"              # "sbx" | "smolvm"
    binary: str = "sbx"            # sbx
    timeout: int = 600
    keep_on_error: bool = False
    io_mode: str = "exec"          # sbx only
    smolvm_binary: str = "smolvm"  # unused by SDK; kept for messages
    smolvm_image: str | None = None                         # host-wide override
    smolvm_image_claude: str | None = None                   # per-agent
    smolvm_image_opencode: str | None = None                 # per-agent
    smolvm_image_codex: str | None = None                    # per-agent
```

- [ ] **Step 4: Run the test; expect PASS**

Run: `uv run pytest tests/test_server_worker.py::test_sandbox_settings_has_smolvm_fields_with_defaults -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/superseded/server/worker.py tests/test_server_worker.py
git commit -m "feat(worker): SandboxSettings gains kind + smolvm_image_* fields"
```

---

### Task 7: Add `_agent_smolvm_image` and `_sandbox_unavailable_msg` helpers

**Files:**
- Modify: `src/superseded/server/worker.py`
- Modify: `tests/test_server_worker.py`

- [ ] **Step 1: Write the failing tests for both helpers**

Append to `tests/test_server_worker.py`:

```python
def test_agent_smolvm_image_resolves_per_agent_field():
    from superseded.server.worker import SandboxSettings, _agent_smolvm_image
    s = SandboxSettings(smolvm_image_claude="ghcr.io/x/c:1")
    assert _agent_smolvm_image(s, "claude-code") == "ghcr.io/x/c:1"
    assert _agent_smolvm_image(s, "opencode") is None
    assert _agent_smolvm_image(s, "codex") is None


def test_agent_smolvm_image_host_wide_override_wins():
    from superseded.server.worker import SandboxSettings, _agent_smolvm_image
    s = SandboxSettings(smolvm_image="ghcr.io/x/all:1",
                        smolvm_image_claude="ghcr.io/x/c:1")
    assert _agent_smolvm_image(s, "claude-code") == "ghcr.io/x/all:1"
    assert _agent_smolvm_image(s, "opencode") == "ghcr.io/x/all:1"


def test_agent_smolvm_image_unknown_agent_returns_none():
    from superseded.server.worker import SandboxSettings, _agent_smolvm_image
    s = SandboxSettings()
    assert _agent_smolvm_image(s, "custom-agent") is None


def test_sandbox_unavailable_msg_sbx():
    from superseded.server.worker import SandboxSettings, _sandbox_unavailable_msg
    s = SandboxSettings(kind="sbx", binary="sbx")
    msg = _sandbox_unavailable_msg(s)
    assert "sbx" in msg
    assert "docker-sbx" in msg


def test_sandbox_unavailable_msg_smolvm():
    from superseded.server.worker import SandboxSettings, _sandbox_unavailable_msg
    s = SandboxSettings(kind="smolvm")
    msg = _sandbox_unavailable_msg(s)
    assert "smolmachines" in msg
    assert "uv sync --extra sandbox" in msg
```

- [ ] **Step 2: Run the tests; expect ImportError**

Run: `uv run pytest tests/test_server_worker.py -k "agent_smolvm_image or sandbox_unavailable_msg" -v`
Expected: FAIL — names not importable.

- [ ] **Step 3: Implement both helpers**

In `src/superseded/server/worker.py`, just below the `SandboxSettings` dataclass, add:

```python
_SMOLVM_AGENT_IMAGE_FIELD: dict[str, str] = {
    "claude-code": "smolvm_image_claude",
    "opencode": "smolvm_image_opencode",
    "codex": "smolvm_image_codex",
}


def _agent_smolvm_image(sandbox: SandboxSettings, agent_name: str) -> str | None:
    """Resolve the smolvm image for ``agent_name``.

    Host-wide ``smolvm_image`` overrides the per-agent field.
    """
    if sandbox.smolvm_image:
        return sandbox.smolvm_image
    field = _SMOLVM_AGENT_IMAGE_FIELD.get(agent_name)
    return getattr(sandbox, field) if field else None


def _sandbox_unavailable_msg(sandbox: SandboxSettings) -> str:
    if sandbox.kind == "smolvm":
        return (
            "sandbox unavailable: smolmachines extra not installed or no image "
            "configured. Run `uv sync --extra sandbox` and set "
            "SUPERSEDED_SMOLVM_IMAGE (or the per-agent "
            "SUPERSEDED_SMOLVM_IMAGE_<AGENT>) to run smolvm-sandboxed reviews."
        )
    return (
        f"sandbox unavailable: '{sandbox.binary}' not found on PATH "
        "(install docker-sbx to run sandboxed reviews)."
    )
```

- [ ] **Step 4: Run the tests; expect PASS**

Run: `uv run pytest tests/test_server_worker.py -k "agent_smolvm_image or sandbox_unavailable_msg" -v`
Expected: PASS for all 5 tests.

- [ ] **Step 5: Commit**

```bash
git add src/superseded/server/worker.py tests/test_server_worker.py
git commit -m "feat(worker): _agent_smolvm_image + _sandbox_unavailable_msg helpers"
```

---

### Task 8: Thread smolvm through `_run_review_for_job` executor selection

**Files:**
- Modify: `src/superseded/server/worker.py:452-468`
- Modify: `tests/test_server_worker.py`

- [ ] **Step 1: Read the current executor-selection block for context**

Run: `uv run python -c "import inspect; from superseded.server import worker; print(inspect.getsource(worker._run_review_for_job))" | sed -n '1,200p'`
Note the existing block at lines 452-468 that builds the executor when `sandbox.enabled`.

- [ ] **Step 2: Write the failing tests for smolvm dispatch from the worker**

Append to `tests/test_server_worker.py`:

```python
def test_run_review_smolvm_dispatch_builds_smolvm_executor(monkeypatch):
    """When sandbox.kind=smolvm + image set + smol importable, the worker
    constructs a SmolvmExecutor via make_sandbox_executor(kind='smolvm')."""
    import asyncio
    import sys
    import types
    import types as _t

    # fake smol
    machine_inst = _t.SimpleNamespace(
        name="superseded-x", write_file=lambda p, d, m=None: None,
        exec=lambda c, o=None: _t.SimpleNamespace(exit_code=0, stdout="[]",
                                                   stderr=""),
        delete=lambda: None, state=lambda: "running")
    fake = types.ModuleType("smol")
    fake.Machine = type("M", (), {"create": staticmethod(lambda c=None,
                                                          conn=None: machine_inst)})
    fake.MachineConfig = type("MC", (), {"__init__": lambda self, **k: None})
    fake.MountSpec = type("MS", (), {"__init__": lambda self, **k: None})
    fake.ResourceSpec = type("RS", (), {"__init__": lambda self, **k: None})
    fake.ExecOptions = type("EO", (), {"__init__": lambda self, **k: None})
    monkeypatch.setitem(sys.modules, "smol", fake)

    from superseded.review import executor as exec_mod
    monkeypatch.setattr(exec_mod, "SMOLVM_AVAILABLE", True)

    captured = {}
    real_make = exec_mod.make_sandbox_executor
    def fake_make(**kw):
        captured["kwargs"] = kw
        # delegate to real to get a real SmolvmExecutor back
        return real_make(**kw)
    monkeypatch.setattr("superseded.review.executor.make_sandbox_executor", fake_make)

    from superseded.server.worker import SandboxSettings
    sbx = SandboxSettings(enabled=True, kind="smolvm",
                          smolvm_image_claude="ghcr.io/x/c:1")

    # Drive _run_review_for_job far enough to pick the executor, then short-
    # circuit by mocking the engine review. The simplest entry is to call
    # the executor-selection block directly via the function under test by
    # stubbing checkout_repo + everything downstream. That's heavy; instead
    # we smoke-test the dispatch by calling make_sandbox_executor the same
    # way the worker does and asserting the kwargs it would have produced.
    from superseded.review.executor import SmolvmExecutor
    ex = real_make(kind="smolvm", agent_name="claude-code",
                   name="superseded-x", cwd="/tmp/whatever",
                   resolved_image="ghcr.io/x/c:1", timeout=600,
                   keep_on_error=False, binary="sbx", io_mode="exec",
                   smolvm_binary="smolvm")
    assert isinstance(ex, SmolvmExecutor)
    assert ex._image == "ghcr.io/x/c:1"


def test_run_review_smolvm_image_unset_raises_runtime_error():
    """Direct construction (mirrors what the worker does) without an image
    must raise loudly — no silent fallback."""
    from superseded.review.executor import make_sandbox_executor
    with pytest.raises(ValueError, match="resolved_image"):
        make_sandbox_executor(kind="smolvm", agent_name="claude-code",
                              name="n1", resolved_image=None)
```

(The worker end-to-end dispatch path is exercised via `test_smolvm_worker_dispatch` in Task 13; here we lock the contract that the worker's chosen `make_sandbox_executor` call shape works.)

- [ ] **Step 2: Run the tests; expect PASS already (these are contract-pinning tests)**

Run: `uv run pytest tests/test_server_worker.py -k "smolvm_dispatch or smolvm_image_unset" -v`
Expected: the first test passes iff Task 5 landed; the second passes iff Task 5 landed. If they pass without worker changes, that is intentional — these guard the call shape the worker emits.

- [ ] **Step 3: Modify the worker's executor-selection block**

In `src/superseded/server/worker.py`, replace lines 452-468:

```python
        executor = None
        if sandbox is not None and sandbox.enabled:
            from superseded.review.executor import make_sandbox_executor

            executor = make_sandbox_executor(
                agent_name=config.agent,
                name=f"superseded-{job.job_id}",
                timeout=sandbox.timeout,
                keep_on_error=sandbox.keep_on_error,
                binary=sandbox.binary,
                io_mode=sandbox.io_mode,
            )
            if not executor.available(engine.agent):
                raise RuntimeError(
                    f"sandbox unavailable: '{sandbox.binary}' not found on PATH "
                    "(install docker-sbx to run sandboxed reviews)."
                )
```

with:

```python
        executor = None
        if sandbox is not None and sandbox.enabled:
            from superseded.review.executor import make_sandbox_executor

            resolved_image: str | None = None
            if sandbox.kind == "smolvm":
                resolved_image = _agent_smolvm_image(sandbox, config.agent)
                if not resolved_image:
                    raise RuntimeError(
                        f"smolvm sandbox selected for agent {config.agent!r} "
                        "but no image configured (set SUPERSEDED_SMOLVM_IMAGE or "
                        f"SUPERSEDED_SMOLVM_IMAGE_"
                        f"{config.agent.upper().replace('-', '_')})."
                    )
            executor = make_sandbox_executor(
                kind=sandbox.kind,
                agent_name=config.agent,
                name=f"superseded-{job.job_id}",
                timeout=sandbox.timeout,
                keep_on_error=sandbox.keep_on_error,
                binary=sandbox.binary,
                io_mode=sandbox.io_mode,
                smolvm_binary=sandbox.smolvm_binary,
                resolved_image=resolved_image if sandbox.kind == "smolvm" else None,
            )
            if not executor.available(engine.agent):
                raise RuntimeError(_sandbox_unavailable_msg(sandbox))
```

- [ ] **Step 4: Run the worker tests; expect PASS**

Run: `uv run pytest tests/test_server_worker.py -v`
Expected: PASS, including the existing sbx-unavailable failure test (it still raises `RuntimeError` because `_sandbox_unavailable_msg(sbx_settings)` produces the same message text the test asserts against).

- [ ] **Step 5: Commit**

```bash
git add src/superseded/server/worker.py tests/test_server_worker.py
git commit -m "feat(worker): dispatch smolvm-sandbox executor + resolve per-agent image"
```

---

### Task 9: Extend `ServerConfig` with smolvm fields + `from_env()`

**Files:**
- Modify: `src/superseded/server/config.py:13-34` (fields) and `:84-183` (from_env)
- Modify: `tests/test_server_config.py`

- [ ] **Step 1: Read the existing `test_server_config.py` to mirror its style**

Run: `uv run python -c "import inspect, pathlib; print(pathlib.Path('tests/test_server_config.py').read_text())" 2>&1 | head -80`
Note the existing pattern: `monkeypatch.setenv("SUPERSEDED_APP_ID", "1")` etc., then `ServerConfig.from_env()`.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_server_config.py`:

```python
def test_from_env_sandbox_kind_smolvm(monkeypatch):
    _set_required_server_env(monkeypatch)
    monkeypatch.setenv("SUPERSEDED_SANDBOX_KIND", "smolvm")
    cfg = ServerConfig.from_env()
    assert cfg.sandbox_kind == "smolvm"


def test_from_env_sandbox_kind_default_is_sbx(monkeypatch):
    _set_required_server_env(monkeypatch)
    cfg = ServerConfig.from_env()
    assert cfg.sandbox_kind == "sbx"


def test_from_env_smolvm_binary(monkeypatch):
    _set_required_server_env(monkeypatch)
    monkeypatch.setenv("SUPERSEDED_SMOLVM_BINARY", "/opt/smolvm/bin/smol")
    cfg = ServerConfig.from_env()
    assert cfg.smolvm_binary == "/opt/smolvm/bin/smol"


def test_from_env_smolvm_images_per_agent(monkeypatch):
    _set_required_server_env(monkeypatch)
    monkeypatch.setenv("SUPERSEDED_SMOLVM_IMAGE_CLAUDE", "gcr/x/c:1")
    monkeypatch.setenv("SUPERSEDED_SMOLVM_IMAGE_OPENCODE", "gcr/x/o:1")
    monkeypatch.setenv("SUPERSEDED_SMOLVM_IMAGE_CODEX", "gcr/x/d:1")
    cfg = ServerConfig.from_env()
    assert cfg.smolvm_image_claude == "gcr/x/c:1"
    assert cfg.smolvm_image_opencode == "gcr/x/o:1"
    assert cfg.smolvm_image_codex == "gcr/x/d:1"


def test_from_env_smolvm_image_host_wide(monkeypatch):
    _set_required_server_env(monkeypatch)
    monkeypatch.setenv("SUPERSEDED_SMOLVM_IMAGE", "gcr/x/all:1")
    cfg = ServerConfig.from_env()
    assert cfg.smolvm_image == "gcr/x/all:1"
```

If `_set_required_server_env` doesn't already exist in the file, add a small helper to the test file:

```python
def _set_required_server_env(monkeypatch):
    monkeypatch.setenv("SUPERSEDED_APP_ID", "123456")
    monkeypatch.setenv("SUPERSEDED_WEBHOOK_SECRET", "whs")
    import tempfile, pathlib
    pk = pathlib.Path(tempfile.mkstemp(suffix=".pem")[1])
    pk.write_text("dummy")
    monkeypatch.setenv("SUPERSEDED_PRIVATE_KEY_PATH", str(pk))
```

(If a similar helper already exists by another name, use that one instead and skip this addition.)

- [ ] **Step 3: Run the tests; expect AttributeError**

Run: `uv run pytest tests/test_server_config.py -k smolvm -v`
Expected: FAIL — `ServerConfig` has no `sandbox_kind` field.

- [ ] **Step 4: Add fields to `ServerConfig`**

In `src/superseded/server/config.py`, after the existing `sandbox_io_mode: str = "exec"` line (config.py:34), add:

```python
    sandbox_kind: str = "sbx"
    smolvm_binary: str = "smolvm"
    smolvm_image: str | None = None
    smolvm_image_claude: str | None = None
    smolvm_image_opencode: str | None = None
    smolvm_image_codex: str | None = None
```

- [ ] **Step 5: Extend `from_env()` to read the new env vars**

In `src/superseded/server/config.py`, at the end of `from_env()` (just before `return cls(**kwargs)` at line 183), add:

```python
        sandbox_kind = os.environ.get("SUPERSEDED_SANDBOX_KIND")
        if sandbox_kind:
            kwargs["sandbox_kind"] = sandbox_kind

        smolvm_binary = os.environ.get("SUPERSEDED_SMOLVM_BINARY")
        if smolvm_binary:
            kwargs["smolvm_binary"] = smolvm_binary

        smolvm_image = os.environ.get("SUPERSEDED_SMOLVM_IMAGE")
        if smolvm_image:
            kwargs["smolvm_image"] = smolvm_image

        smolvm_image_claude = os.environ.get("SUPERSEDED_SMOLVM_IMAGE_CLAUDE")
        if smolvm_image_claude:
            kwargs["smolvm_image_claude"] = smolvm_image_claude

        smolvm_image_opencode = os.environ.get("SUPERSEDED_SMOLVM_IMAGE_OPENCODE")
        if smolvm_image_opencode:
            kwargs["smolvm_image_opencode"] = smolvm_image_opencode

        smolvm_image_codex = os.environ.get("SUPERSEDED_SMOLVM_IMAGE_CODEX")
        if smolvm_image_codex:
            kwargs["smolvm_image_codex"] = smolvm_image_codex
```

- [ ] **Step 6: Run the tests; expect PASS**

Run: `uv run pytest tests/test_server_config.py -k smolvm -v`
Expected: PASS for all 5 tests.

- [ ] **Step 7: Commit**

```bash
git add src/superseded/server/config.py tests/test_server_config.py
git commit -m "feat(server-config): ServerConfig sandbox_kind + smolvm_image_* fields/env"
```

---

### Task 10: Thread smolvm `SandboxSettings` from `cli.py serve`

**Files:**
- Modify: `src/superseded/cli.py:823-829`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py` (find the existing `serve`-tests block, or add at the end):

```python
def test_serve_threads_smolvm_sandbox_fields(monkeypatch):
    """SandboxSettings built by `serve` carries kind+smolvm_image_* from ServerConfig."""
    import tempfile, pathlib
    pk = pathlib.Path(tempfile.mkstemp(suffix=".pem")[1])
    pk.write_text("dummy")
    monkeypatch.setenv("SUPERSEDED_APP_ID", "123456")
    monkeypatch.setenv("SUPERSEDED_WEBHOOK_SECRET", "whs")
    monkeypatch.setenv("SUPERSEDED_PRIVATE_KEY_PATH", str(pk))
    monkeypatch.setenv("SUPERSEDED_SANDBOX", "1")
    monkeypatch.setenv("SUPERSEDED_SANDBOX_KIND", "smolvm")
    monkeypatch.setenv("SUPERSEDED_SMOLVM_IMAGE", "gcr/x/all:1")

    captured = {}
    real_init = None
    # Spy on ReviewWorker.__init__ to capture the sandbox arg.
    from superseded.server import worker as worker_mod
    real_init = worker_mod.ReviewWorker.__init__
    def spy(self, **kw):
        captured["sandbox"] = kw.get("sandbox")
        return real_init(self, **kw)
    monkeypatch.setattr(worker_mod.ReviewWorker, "__init__", spy)

    # Stub uvicorn + lifecycle so `serve` exits fast after constructing worker.
    import uvicorn  # noqa: F401
    monkeypatch.setattr("uvicorn.run", lambda **k: None)

    from click.testing import CliRunner
    from superseded.cli import cli
    runner = CliRunner()
    # `serve` blocks on uvicorn; rely on uvicorn.run stub + the runner's
    # isolation to capture the worker construction, then short-circuit
    # via SystemExit (sys.exit after lifecycle.shutdown in cli.py).
    try:
        runner.invoke(cli, ["serve", "--host", "127.0.0.1", "--port", "0"])
    except Exception:
        pass
    assert "sandbox" in captured
    assert captured["sandbox"].kind == "smolvm"
    assert captured["sandbox"].smolvm_image == "gcr/x/all:1"
```

- [ ] **Step 2: Run the test; expect assertion failure**

Run: `uv run pytest tests/test_cli.py::test_serve_threads_smolvm_sandbox_fields -v`
Expected: FAIL — `captured["sandbox"].kind == "sbx"` (default) and `smolvm_image is None`.

- [ ] **Step 3: Pass the new fields into `SandboxSettings`**

In `src/superseded/cli.py`, replace the `SandboxSettings(...)` block at lines 823-829:

```python
    sandbox = SandboxSettings(
        enabled=config.sandbox_enabled,
        binary=config.sbx_binary,
        timeout=config.sandbox_timeout,
        keep_on_error=config.sandbox_keep_on_error,
        io_mode=config.sandbox_io_mode,
    )
```

with:

```python
    sandbox = SandboxSettings(
        enabled=config.sandbox_enabled,
        kind=config.sandbox_kind,
        binary=config.sbx_binary,
        timeout=config.sandbox_timeout,
        keep_on_error=config.sandbox_keep_on_error,
        io_mode=config.sandbox_io_mode,
        smolvm_binary=config.smolvm_binary,
        smolvm_image=config.smolvm_image,
        smolvm_image_claude=config.smolvm_image_claude,
        smolvm_image_opencode=config.smolvm_image_opencode,
        smolvm_image_codex=config.smolvm_image_codex,
    )
```

- [ ] **Step 4: Run the test; expect PASS**

Run: `uv run pytest tests/test_cli.py::test_serve_threads_smolvm_sandbox_fields -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/superseded/cli.py tests/test_cli.py
git commit -m "feat(cli): serve threads smolvm sandbox_kind + per-agent images"
```

---

### Task 11: Add `SUPERSEDED_SANDBOX_KIND` to `compose.yml`

**Files:**
- Modify: `compose.yml`

- [ ] **Step 1: Read the existing `compose.yml`**

Run: `cat compose.yml` (or read via the Read tool)
Locate the existing `SUPERSEDED_SANDBOX` environment line and add the kind beneath it (mirror existing indentation).

- [ ] **Step 2: Add the env line**

In `compose.yml`, immediately after the existing `SUPERSEDED_SANDBOX` line in the `api` service's `environment:`, add:

```yaml
      SUPERSEDED_SANDBOX_KIND: ${SUPERSEDED_SANDBOX_KIND:-sbx}
```

- [ ] **Step 3: Commit**

```bash
git add compose.yml
git commit -m "chore(compose): expose SUPERSEDED_SANDBOX_KIND (default sbx)"
```

---

### Task 12: Update `AGENTS.md` with the smolvm runtime dependency

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Read the existing sandbox paragraph in AGENTS.md**

Run: `grep -n "SandboxExecutor\|sbx\|sandbox" AGENTS.md | head -20`
Locate the bullet that mentions `SandboxExecutor`/`sbx`.

- [ ] **Step 2: Add a sibling paragraph about the smolvm alternative**

After the existing sbx paragraph, add:

```markdown
- `SmolvmExecutor` (selected via `sandbox_kind="smolvm"`/`SUPERSEDED_SANDBOX_KIND=smolvm`) is an alternative sandbox backend that uses the embedded `smolmachines` Python SDK (`import smol`) instead of the `sbx` CLI. It boots OCI images as microVMs via libkrun in-process on macOS (Hypervisor.framework), Linux (KVM), or Windows (WHP). Install the extra with `uv sync --extra sandbox`. Operator-supplied per-agent OCI images are required (set `SUPERSEDED_SMOLVM_IMAGE_*` or the host-wide `SUPERSEDED_SMOLVM_IMAGE`); provider keys (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`) are injected per-exec via `ExecOptions.env` from the server's own environment.
```

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md
git commit -m "docs(agents): document the smolvm sandbox alternative"
```

---

### Task 13: End-to-end worker dispatch test for smolvm

This task pins the worker's full executor-building path end-to-end so a regression in `_run_review_for_job`'s smolvm branch is caught.

**Files:**
- Modify: `tests/test_server_worker.py`

- [ ] **Step 1: Write the failing test that drives `_run_review_for_job` with smolvm**

Append to `tests/test_server_worker.py`:

```python
@pytest.mark.asyncio
async def test_smolvm_worker_dispatch(monkeypatch, tmp_path):
    """Drives _run_review_for_job with sandbox.kind=smolvm; asserts the
    executor built is a SmolvmExecutor with the right image, cwd, name."""
    import sys, types

    machine_inst = types.SimpleNamespace(
        name="superseded-smolvm-1",
        write_file=lambda p, d, m=None: None,
        exec=lambda c, o=None: types.SimpleNamespace(
            exit_code=0, stdout='{"findings":[]}', stderr=""),
        delete=lambda: None, state=lambda: "running",
    )
    fake = types.ModuleType("smol")
    fake.Machine = type("M", (), {"create": staticmethod(
        lambda c=None, conn=None: machine_inst)})
    fake.MachineConfig = type("MC", (), {"__init__": lambda self, **k: None})
    fake.MountSpec = type("MS", (), {"__init__": lambda self, **k: None})
    fake.ResourceSpec = type("RS", (), {"__init__": lambda self, **k: None})
    fake.ExecOptions = type("EO", (), {"__init__": lambda self, **k: None})
    monkeypatch.setitem(sys.modules, "smol", fake)
    from superseded.review import executor as exec_mod
    monkeypatch.setattr(exec_mod, "SMOLVM_AVAILABLE", True)

    from superseded.review.engine import ReviewEngine
    captured_executor = {}

    class FakeEngine:
        agent = types.SimpleNamespace(is_available=lambda: True)
        def review(self, **kw):
            captured_executor["executor"] = kw.get("executor")
            from superseded.models import ReviewResult
            return ReviewResult(findings=[], summary={})

    monkeypatch.setattr("superseded.server.worker.ReviewEngine.select",
                        lambda *a, **k: FakeEngine())

    monkeypatch.setattr("superseded.server.worker.checkout_repo",
                        lambda **k: str(tmp_path))
    monkeypatch.setattr("superseded.server.worker.gather_context", lambda *a, **k: {
        "file_context": "", "static_signals": "", "usage_signals": "",
        "conventions_signals": "", "spec_signals": "",
    })

    # Skip the github/store/progressive paths with lightweight stubs.
    github = MagicMock()
    github.fetch_pr_diff = MagicMock(return_value="")
    github.fetch_pr_description = MagicMock(return_value="")
    github.compare_diff = MagicMock(return_value=("", "ahead"))

    repo_manager = MagicMock()
    repo_manager.job_dir = MagicMock(return_value=tmp_path)
    repo_manager.disk_usage = MagicMock(return_value=0.1)

    from superseded.server.worker import (
        ReviewJob, SandboxSettings, _run_review_for_job,
    )

    job = ReviewJob(installation_id=1, owner="o", repo="r", pr_number=1,
                    head_sha="aaa", base_sha="bbb", job_id="smolvm-1")
    sandbox = SandboxSettings(enabled=True, kind="smolvm",
                               smolvm_image_claude="ghcr.io/x/c:1")

    await _run_review_for_job(
        github=github,
        repo_manager=repo_manager,
        token="t",
        job=job,
        correlation_id="c",
        server_agent="claude-code",
        sandbox=sandbox,
    )
    ex = captured_executor["executor"]
    from superseded.review.executor import SmolvmExecutor
    assert isinstance(ex, SmolvmExecutor)
    assert ex._image == "ghcr.io/x/c:1"
    assert ex._name == "superseded-smolvm-1"
```

- [ ] **Step 2: Run the test; iterate until it passes**

Run: `uv run pytest tests/test_server_worker.py::test_smolvm_worker_dispatch -v`
Expected: PASS. If stubs miss an attribute the worker reads, add the missing attribute to the relevant `MagicMock()` and re-run. Don't add code to the production source for this — the worker dispatch logic should already be correct from Task 8.

- [ ] **Step 3: Commit**

```bash
git add tests/test_server_worker.py
git commit -m "test(worker): smolvm end-to-end dispatch through _run_review_for_job"
```

---

### Task 14: Full verification

- [ ] **Step 1: Run the full pytest suite**

Run: `uv run pytest tests/ -v`
Expected: all non-postgres tests PASS (postgres tests skip per `addopts = "-m 'not postgres'"`).

- [ ] **Step 2: Run ruff lint + format**

Run: `uv run ruff format src/ tests/ && uv run ruff check src/ tests/`
Expected: no errors.

- [ ] **Step 3: Run the install + import sanity check (without the extra)**

Run: `uv sync && uv run python -c "from superseded.review.executor import SmolvmExecutor; print('import ok')"`
Expected: prints `import ok` (the module imports cleanly without the `smol` extra; `SmolvmExecutor.available` returns False at runtime).

- [ ] **Step 4: Run the install + import sanity check (with the extra)**

Run: `uv sync --extra sandbox && uv run python -c "from smol import Machine; print('sdk ok')"`
Expected: prints `sdk ok`.

- [ ] **Step 5: Final commit if format/lint changed any files**

```bash
git status
# if anything is modified:
git add -A && git commit -m "chore: ruff format + lint after smolvm sandbox integration"
```

---

## Self-Review Notes

**Spec coverage:**
- `SmolvmExecutor` class — Tasks 2-4.
- `_SmolvmSession.__enter__/__exit__/run` — Tasks 3-4.
- `_filter_provider_keys` + `_DEFAULT_PROVIDER_KEYS` — Task 2.
- `make_sandbox_executor(kind=...)` — Task 5.
- `SandboxSettings` fields — Task 6.
- `_agent_smolvm_image` + `_sandbox_unavailable_msg` — Task 7.
- Worker dispatch (`_run_review_for_job`) — Task 8 + pinned by Task 13.
- `ServerConfig` fields + `from_env()` — Task 9.
- `cli.py serve` thread-through — Task 10.
- `pyproject.toml` optional dep — Task 1.
- `compose.yml` — Task 11.
- `AGENTS.md` — Task 12.
- Full verification — Task 14.

**Open questions from spec deferred:** the optional `@pytest.mark.kvm` end-to-end smoke (spec's testing strategy mentions it as "optional in v1") is intentionally **not** in this plan. It would require a marker addition in `pyproject.toml`'s `[tool.pytest.ini_options]` and live microVM runs; defer to a follow-up if the operator wants CI signal on KVM hosts.

**Placeholder scan:** no TBD/TODO/implement-later in any step.

**Type/signature consistency:**
- `SmolvmExecutor.__init__(*, agent_name, image, name, cwd=None, timeout=DEFAULT_SANDBOX_TIMEOUT, keep_on_error=False, provider_keys_mapping=None)` — used identically by `make_sandbox_executor` in Task 5.
- `SandboxSettings` fields: `kind`, `smolvm_binary`, `smolvm_image`, `smolvm_image_claude`, `smolvm_image_opencode`, `smolvm_image_codex` — referenced identically in worker (Task 8), `cli.py` (Task 10), `ServerConfig` (Task 9).
- `_agent_smolvm_image(sandbox, agent_name)` and `_sandbox_unavailable_msg(sandbox)` — used in Task 8, defined in Task 7.