# Sandbox Executor Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a pluggable agent executor (`SubprocessExecutor` + `SandboxExecutor`) behind a `Session`/`AgentExecutor` Protocol, route the review engine through it, and add a local `--sandbox` toggle — all fully tested with mocked `sbx`/subprocess, no live binaries required.

**Architecture:** `review/engine.py` currently shells out to AI CLIs inline via `subprocess.run`. We extract that into `review/executor.py` as a `Session.run(cmd, prompt, timeout) -> stdout` call, with two backends: `SubprocessExecutor` (today's behavior, default) and `SandboxExecutor` (creates an `sbx` microVM per session, runs each pass via `sbx exec`, tears down with `sbx rm`). The engine opens one session per review and fans the 5 passes out as concurrent `session.run()` calls. A `--sandbox`/`SUPERSEDED_SANDBOX` toggle selects the backend locally; the server wiring comes in a later plan.

**Tech Stack:** Python 3.14+, pytest (`asyncio_mode = "auto"`), click, pydantic v2, ruff (`E,W,F,I,N,UP,B,SIM,TCH,RUF`). Commands run via `uv run …`. All external binaries (`sbx`, agent CLIs, `subprocess`) are mocked in tests.

**Spec:** `docs/superseded/specs/2026-07-01-sandbox-executor-and-server-action-design.md`

---

## File structure

- **Create** `src/superseded/review/executor.py` — `AgentRunError`, `Session`/`AgentExecutor` Protocols, `SubprocessExecutor`, `SandboxExecutor`, `SBX_AGENT_MAP`, `make_sandbox_executor()`. One responsibility: run an agent command and return stdout, whether on the host or in a sandbox.
- **Create** `tests/test_executor.py` — unit tests for both executors (mocked `subprocess.run`/`shutil.which`).
- **Modify** `src/superseded/review/engine.py` — `review()` accepts an `executor`; `run_pass()` runs through a `Session` instead of inline `subprocess.run`.
- **Modify** `tests/test_engine.py` — move subprocess-behavior tests to `test_executor.py`; update `run_pass`/`review` tests for the new signatures.
- **Modify** `src/superseded/config.py` — add `sandbox: bool = False` to `Config`.
- **Modify** `src/superseded/cli.py` — `SANDBOX_ENV`, `resolve_sandbox()`, `--sandbox/--no-sandbox` flag, `_select_executor()`, wire executor into `_run_review()`.
- **Modify** `tests/test_cli.py` — `resolve_sandbox` tests, sandbox executor-selection tests, update the `fake_run_pass` signature.
- **Modify** `AGENTS.md` — document `sbx` as a runtime external dependency and the executor toggle.

---

## Task 1: Optional spike — confirm `sbx exec` semantics

This task is **manual investigation**, not code. It determines the default of `sandbox_io_mode` (set in the later server plan). Both I/O modes ship and are fully tested regardless, so this does not block implementation. Skip if no KVM host with `sbx` is available.

**Files:** none (record findings in the commit message of Task 6).

- [ ] **Step 1: Verify `sbx exec` stdin/stdout/exit-code pass-through**

On a KVM-capable host with `docker-sbx` installed and `sbx login` done:

```bash
sbx create --name spike-test shell /tmp
echo "hello-from-stdin" | sbx exec spike-test -- bash -c 'cat; exit 0'
echo "real-stdout"; sbx exec spike-test -- bash -c 'echo out; echo err 1>&2; exit 3'
sbx rm spike-test
```

Record: (a) Did the first command print `hello-from-stdin`? (confirms stdin forwarding.)
(b) Did the second print `out` on stdout and `err` on stderr, and did `sbx exec` exit 3?
(confirms stdout/stderr/exit-code propagation.)

- [ ] **Step 2: Decide the default I/O mode**

If Step 1 confirms stdin + stdout + exit-code propagation → default `io_mode = "exec"`.
If stdin is NOT forwarded → default `io_mode = "cp"` (file-based fallback).
Record the decision. Both modes are implemented in Tasks 4–5 regardless.

---

## Task 2: `AgentRunError` + executor Protocols

**Files:**
- Create: `src/superseded/review/executor.py`
- Test: `tests/test_executor.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_executor.py`:

```python
from __future__ import annotations

from superseded.review.executor import AgentRunError


def test_agent_run_error_is_runtime_error():
    assert issubclass(AgentRunError, RuntimeError)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_executor.py -v`
Expected: FAIL with `ImportError: cannot import name 'AgentRunError'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/superseded/review/executor.py`:

```python
from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from superseded.agents.base import Agent

logger = logging.getLogger(__name__)

DEFAULT_SANDBOX_TIMEOUT = 600


class AgentRunError(RuntimeError):
    """An agent command failed (missing CLI, timeout, or non-zero exit)."""


class Session(Protocol):
    def __enter__(self) -> Session: ...
    def __exit__(self, *exc: object) -> None: ...
    def run(self, cmd: list[str], prompt: str, *, timeout: int) -> str: ...


class AgentExecutor(Protocol):
    def available(self, agent: Agent) -> bool: ...
    def session(
        self, cwd: str | Path | None = None, *, env: dict[str, str] | None = None
    ) -> Session: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_executor.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check src/superseded/review/executor.py tests/test_executor.py && uv run ruff format src/superseded/review/executor.py tests/test_executor.py`

- [ ] **Step 6: Commit**

```bash
git add src/superseded/review/executor.py tests/test_executor.py
git commit -m "feat: add AgentRunError and executor Protocols"
```

---

## Task 3: `SubprocessExecutor`

Extract today's `subprocess.run` logic from `engine.run_pass` verbatim into a session backend.

**Files:**
- Modify: `src/superseded/review/executor.py`
- Test: `tests/test_executor.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_executor.py`:

```python
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from superseded.review.executor import SubprocessExecutor


def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_subprocess_session_returns_stdout():
    executor = SubprocessExecutor()
    with patch("superseded.review.executor.subprocess.run") as mock_run:
        mock_run.return_value = _completed(stdout="agent-output")
        with executor.session() as sess:
            assert sess.run(["claude"], "prompt", timeout=10) == "agent-output"


def test_subprocess_session_forwards_prompt_as_stdin():
    executor = SubprocessExecutor()
    with patch("superseded.review.executor.subprocess.run") as mock_run:
        mock_run.return_value = _completed(stdout="[]")
        with executor.session() as sess:
            sess.run(["claude"], "the-prompt", timeout=10)
    assert mock_run.call_args.kwargs.get("input") == "the-prompt"


def test_subprocess_session_raises_on_missing_cli():
    executor = SubprocessExecutor()
    with patch("superseded.review.executor.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError()
        with executor.session() as sess, pytest.raises(AgentRunError, match="not found"):
            sess.run(["claude"], "p", timeout=10)


def test_subprocess_session_raises_on_timeout():
    executor = SubprocessExecutor()
    with patch("superseded.review.executor.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["claude"], timeout=10)
        with executor.session() as sess, pytest.raises(AgentRunError, match="timed out"):
            sess.run(["claude"], "p", timeout=10)


def test_subprocess_session_raises_on_nonzero_exit():
    executor = SubprocessExecutor()
    with patch("superseded.review.executor.subprocess.run") as mock_run:
        mock_run.return_value = _completed(stderr="auth error", returncode=1)
        with executor.session() as sess, pytest.raises(AgentRunError, match="auth error"):
            sess.run(["claude"], "p", timeout=10)


def test_subprocess_session_forwards_cwd(tmp_path):
    executor = SubprocessExecutor()
    with patch("superseded.review.executor.subprocess.run") as mock_run:
        mock_run.return_value = _completed(stdout="[]")
        with executor.session(cwd=tmp_path) as sess:
            sess.run(["claude"], "p", timeout=10)
    assert mock_run.call_args.kwargs.get("cwd") == str(tmp_path)


def test_subprocess_session_defaults_cwd_to_none():
    executor = SubprocessExecutor()
    with patch("superseded.review.executor.subprocess.run") as mock_run:
        mock_run.return_value = _completed(stdout="[]")
        with executor.session() as sess:
            sess.run(["claude"], "p", timeout=10)
    assert mock_run.call_args.kwargs.get("cwd") is None


def test_subprocess_session_forwards_env():
    executor = SubprocessExecutor()
    env = {"FOO": "bar"}
    with patch("superseded.review.executor.subprocess.run") as mock_run:
        mock_run.return_value = _completed(stdout="[]")
        with executor.session(env=env) as sess:
            sess.run(["claude"], "p", timeout=10)
    assert mock_run.call_args.kwargs.get("env") == env


def test_subprocess_executor_available_delegates_to_agent():
    executor = SubprocessExecutor()
    agent = MagicMock()
    agent.is_available.return_value = True
    assert executor.available(agent) is True
    agent.is_available.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_executor.py -v`
Expected: FAIL (`SubprocessExecutor` not defined / import error).

- [ ] **Step 3: Implement `SubprocessExecutor`**

Append to `src/superseded/review/executor.py`:

```python


class _SubprocessSession:
    def __init__(self, cwd: str | None, env: dict[str, str] | None) -> None:
        self._cwd = cwd
        self._env = env

    def __enter__(self) -> _SubprocessSession:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def run(self, cmd: list[str], prompt: str, *, timeout: int) -> str:
        try:
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self._cwd,
                env=self._env,
            )
        except FileNotFoundError as err:
            raise AgentRunError(
                f"Agent CLI '{cmd[0]}' not found on PATH. "
                "Install it or choose a different agent with --agent."
            ) from err
        except subprocess.TimeoutExpired as err:
            raise AgentRunError(f"Agent timed out after {timeout} seconds.") from err
        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise AgentRunError(
                f"Agent '{cmd[0]}' exited {result.returncode}" + (f": {stderr}" if stderr else "")
            )
        return result.stdout


class SubprocessExecutor:
    """Runs agent CLIs directly as host subprocesses (the default backend)."""

    def available(self, agent: Agent) -> bool:
        return agent.is_available()

    def session(
        self, cwd: str | Path | None = None, *, env: dict[str, str] | None = None
    ) -> Session:
        return _SubprocessSession(str(cwd) if cwd is not None else None, env)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_executor.py -v`
Expected: PASS (all).

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check src/superseded/review/executor.py tests/test_executor.py && uv run ruff format src/superseded/review/executor.py tests/test_executor.py`

- [ ] **Step 6: Commit**

```bash
git add src/superseded/review/executor.py tests/test_executor.py
git commit -m "feat: add SubprocessExecutor backend"
```

---

## Task 4: `SandboxExecutor` — `sbx create/exec/rm` lifecycle (`exec` I/O mode)

**Files:**
- Modify: `src/superseded/review/executor.py`
- Test: `tests/test_executor.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_executor.py`:

```python
from superseded.review.executor import SBX_AGENT_MAP, SandboxExecutor


def test_sbx_agent_map():
    assert SBX_AGENT_MAP["claude-code"] == "claude"
    assert SBX_AGENT_MAP["opencode"] == "opencode"
    assert SBX_AGENT_MAP["codex"] == "codex"


def test_sandbox_executor_available_checks_sbx_on_path(monkeypatch):
    executor = SandboxExecutor(agent_name="claude", name="sbx-1")
    monkeypatch.setattr("superseded.review.executor.shutil.which", lambda cmd: "/usr/bin/sbx")
    assert executor.available(MagicMock()) is True
    monkeypatch.setattr("superseded.review.executor.shutil.which", lambda cmd: None)
    assert executor.available(MagicMock()) is False


def test_sandbox_session_create_runs_sbx_create(tmp_path):
    executor = SandboxExecutor(agent_name="claude", name="sbx-1", cwd=tmp_path)
    with patch("superseded.review.executor.subprocess.run") as mock_run:
        mock_run.return_value = _completed()
        with executor.session():
            pass
    create_call = mock_run.call_args_list[0]
    assert create_call.args[0] == ["sbx", "create", "--name", "sbx-1", "claude", str(tmp_path)]


def test_sandbox_session_create_missing_sbx_raises(tmp_path):
    executor = SandboxExecutor(agent_name="claude", name="sbx-1", cwd=tmp_path)
    with patch("superseded.review.executor.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError()
        with pytest.raises(AgentRunError, match="sbx"), executor.session():
            pass


def test_sandbox_session_run_uses_sbx_exec_with_prompt_as_stdin(tmp_path):
    executor = SandboxExecutor(agent_name="claude", name="sbx-1", cwd=tmp_path)
    with patch("superseded.review.executor.subprocess.run") as mock_run:
        mock_run.return_value = _completed(stdout="[]")
        with executor.session() as sess:
            assert sess.run(["claude", "-p"], "the-prompt", timeout=42) == "[]"
    exec_call = mock_run.call_args_list[1]
    assert exec_call.args[0] == ["sbx", "exec", "sbx-1", "--", "claude", "-p"]
    assert exec_call.kwargs.get("input") == "the-prompt"
    assert exec_call.kwargs.get("timeout") == 42


def test_sandbox_session_run_nonzero_raises(tmp_path):
    executor = SandboxExecutor(agent_name="claude", name="sbx-1", cwd=tmp_path)
    with patch("superseded.review.executor.subprocess.run") as mock_run:
        mock_run.side_effect = [_completed(), _completed(stderr="boom", returncode=2), _completed()]
        with executor.session() as sess, pytest.raises(AgentRunError, match="boom"):
            sess.run(["claude"], "p", timeout=10)


def test_sandbox_session_run_timeout_raises(tmp_path):
    executor = SandboxExecutor(agent_name="claude", name="sbx-1", cwd=tmp_path)
    with patch("superseded.review.executor.subprocess.run") as mock_run:
        mock_run.side_effect = [_completed(), subprocess.TimeoutExpired(cmd=[], timeout=10), _completed()]
        with executor.session() as sess, pytest.raises(AgentRunError, match="timed out"):
            sess.run(["claude"], "p", timeout=10)


def test_sandbox_session_exit_runs_sbx_rm(tmp_path):
    executor = SandboxExecutor(agent_name="claude", name="sbx-1", cwd=tmp_path)
    with patch("superseded.review.executor.subprocess.run") as mock_run:
        mock_run.return_value = _completed()
        with executor.session():
            pass
    rm_call = mock_run.call_args_list[-1]
    assert rm_call.args[0] == ["sbx", "rm", "sbx-1"]


def test_sandbox_session_keep_on_error_skips_rm(tmp_path):
    executor = SandboxExecutor(
        agent_name="claude", name="sbx-1", cwd=tmp_path, keep_on_error=True
    )
    with patch("superseded.review.executor.subprocess.run") as mock_run:
        mock_run.side_effect = [_completed(), _completed(stderr="x", returncode=1)]
        with executor.session() as sess, pytest.raises(AgentRunError):
            sess.run(["claude"], "p", timeout=10)
    cmds = [c.args[0] for c in mock_run.call_args_list]
    assert ["sbx", "rm", "sbx-1"] not in cmds


def test_sandbox_executor_requires_cwd():
    executor = SandboxExecutor(agent_name="claude", name="sbx-1")
    with pytest.raises(ValueError, match="cwd"):
        executor.session()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_executor.py -v`
Expected: FAIL (`SandboxExecutor` / `SBX_AGENT_MAP` not defined).

- [ ] **Step 3: Implement `SBX_AGENT_MAP` and `SandboxExecutor`**

Add `SBX_AGENT_MAP` near the top constants in `src/superseded/review/executor.py` (after `DEFAULT_SANDBOX_TIMEOUT`):

```python
SBX_AGENT_MAP: dict[str, str] = {
    "claude-code": "claude",
    "opencode": "opencode",
    "codex": "codex",
}
```

Append the session + executor classes to `src/superseded/review/executor.py`:

```python


class _SandboxSession:
    def __init__(
        self,
        binary: str,
        name: str,
        sbx_agent: str,
        cwd: str,
        timeout: int,
        keep_on_error: bool,
        io_mode: str,
    ) -> None:
        self._binary = binary
        self._name = name
        self._sbx_agent = sbx_agent
        self._cwd = cwd
        self._timeout = timeout
        self._keep_on_error = keep_on_error
        self._io_mode = io_mode
        self._errored = False

    def __enter__(self) -> _SandboxSession:
        try:
            subprocess.run(
                [self._binary, "create", "--name", self._name, self._sbx_agent, self._cwd],
                capture_output=True,
                text=True,
                check=True,
                timeout=self._timeout,
            )
        except FileNotFoundError as err:
            raise AgentRunError(
                f"'{self._binary}' not found on PATH. "
                "Install Docker Sandboxes (docker-sbx) to use sandbox execution."
            ) from err
        except subprocess.CalledProcessError as err:
            raise AgentRunError(
                f"sbx create failed (exit {err.returncode}): {(err.stderr or '').strip()}"
            ) from err
        return self

    def __exit__(self, *exc: object) -> None:
        if self._keep_on_error and self._errored:
            logger.warning("keep_on_error: leaving sandbox %s for inspection", self._name)
            return None
        try:
            subprocess.run(
                [self._binary, "rm", self._name],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except Exception:
            logger.warning("sbx rm failed for sandbox %s", self._name)
        return None

    def run(self, cmd: list[str], prompt: str, *, timeout: int) -> str:
        if self._io_mode == "cp":
            return self._run_via_file(cmd, prompt, timeout=timeout)
        try:
            result = subprocess.run(
                [self._binary, "exec", self._name, "--", *cmd],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError as err:
            self._errored = True
            raise AgentRunError(f"'{self._binary}' not found on PATH.") from err
        except subprocess.TimeoutExpired as err:
            self._errored = True
            raise AgentRunError(f"Agent timed out after {timeout} seconds.") from err
        if result.returncode != 0:
            self._errored = True
            stderr = result.stderr.strip()
            raise AgentRunError(
                f"Agent '{cmd[0]}' exited {result.returncode}" + (f": {stderr}" if stderr else "")
            )
        return result.stdout

    def _run_via_file(self, cmd: list[str], prompt: str, *, timeout: int) -> str:
        raise NotImplementedError


class SandboxExecutor:
    """Runs agent CLIs inside an `sbx` microVM sandbox (one per session)."""

    def __init__(
        self,
        *,
        binary: str = "sbx",
        agent_name: str,
        name: str,
        cwd: str | Path | None = None,
        timeout: int = DEFAULT_SANDBOX_TIMEOUT,
        keep_on_error: bool = False,
        io_mode: str = "exec",
    ) -> None:
        self._binary = binary
        self._agent_name = agent_name
        self._name = name
        self._cwd = cwd
        self._timeout = timeout
        self._keep_on_error = keep_on_error
        self._io_mode = io_mode

    def available(self, agent: Agent) -> bool:
        return shutil.which(self._binary) is not None

    def session(
        self, cwd: str | Path | None = None, *, env: dict[str, str] | None = None
    ) -> Session:
        resolved = cwd if cwd is not None else self._cwd
        if resolved is None:
            raise ValueError(
                "SandboxExecutor requires a cwd (the repo checkout) for the sandbox workspace."
            )
        return _SandboxSession(
            binary=self._binary,
            name=self._name,
            sbx_agent=self._agent_name,
            cwd=str(resolved),
            timeout=self._timeout,
            keep_on_error=self._keep_on_error,
            io_mode=self._io_mode,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_executor.py -v`
Expected: PASS (all).

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check src/superseded/review/executor.py tests/test_executor.py && uv run ruff format src/superseded/review/executor.py tests/test_executor.py`

- [ ] **Step 6: Commit**

```bash
git add src/superseded/review/executor.py tests/test_executor.py
git commit -m "feat: add SandboxExecutor with sbx create/exec/rm lifecycle"
```

---

## Task 5: `SandboxExecutor` — `cp` I/O mode fallback

The `cp` mode writes the prompt to a file in the mounted workspace and redirects it into the agent via `bash -c '... < file'` (used if `sbx exec` does not forward stdin). The prompt file lives under `cwd` so the direct-mode mount exposes it at the same absolute path inside the sandbox.

**Files:**
- Modify: `src/superseded/review/executor.py` (`_SandboxSession._run_via_file`)
- Modify: `src/superseded/review/executor.py` (add `import contextlib`)
- Test: `tests/test_executor.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_executor.py`:

```python
def test_sandbox_session_cp_mode_writes_prompt_and_redirects(tmp_path):
    executor = SandboxExecutor(agent_name="claude", name="sbx-1", cwd=tmp_path, io_mode="cp")
    with patch("superseded.review.executor.subprocess.run") as mock_run:
        mock_run.return_value = _completed(stdout="[]")
        with executor.session() as sess:
            out = sess.run(["claude", "-p"], "secret-prompt", timeout=15)
    assert out == "[]"
    exec_call = mock_run.call_args_list[1]
    assert exec_call.args[0][:4] == ["sbx", "exec", "sbx-1", "--"]
    assert exec_call.args[0][4] == "bash"
    assert exec_call.args[0][5] == "-c"
    shell_cmd = exec_call.args[0][6]
    assert "claude -p" in shell_cmd
    assert "<" in shell_cmd
    # prompt file was cleaned up after the run
    leftover = list(tmp_path.glob(".sbx_prompt_*.txt"))
    assert leftover == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_executor.py::test_sandbox_session_cp_mode_writes_prompt_and_redirects -v`
Expected: FAIL (`NotImplementedError`).

- [ ] **Step 3: Implement `_run_via_file`**

Add `import contextlib` to the imports at the top of `src/superseded/review/executor.py` (keep `import` block alphabetical: `contextlib` before `logging`).

Replace the `_run_via_file` stub with:

```python
    def _run_via_file(self, cmd: list[str], prompt: str, *, timeout: int) -> str:
        prompt_path = Path(self._cwd) / f".sbx_prompt_{uuid.uuid4().hex[:8]}.txt"
        prompt_path.write_text(prompt)
        try:
            shell_cmd = f"{shlex.join(cmd)} < {shlex.quote(str(prompt_path))}"
            try:
                result = subprocess.run(
                    [self._binary, "exec", self._name, "--", "bash", "-c", shell_cmd],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except FileNotFoundError as err:
                self._errored = True
                raise AgentRunError(f"'{self._binary}' not found on PATH.") from err
            except subprocess.TimeoutExpired as err:
                self._errored = True
                raise AgentRunError(f"Agent timed out after {timeout} seconds.") from err
            if result.returncode != 0:
                self._errored = True
                stderr = result.stderr.strip()
                raise AgentRunError(
                    f"Agent '{cmd[0]}' exited {result.returncode}"
                    + (f": {stderr}" if stderr else "")
                )
            return result.stdout
        finally:
            with contextlib.suppress(OSError):
                prompt_path.unlink()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_executor.py -v`
Expected: PASS (all, including the new cp-mode test).

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check src/superseded/review/executor.py tests/test_executor.py && uv run ruff format src/superseded/review/executor.py tests/test_executor.py`

- [ ] **Step 6: Commit**

```bash
git add src/superseded/review/executor.py tests/test_executor.py
git commit -m "feat: add sbx cp I/O mode fallback to SandboxExecutor"
```

---

## Task 6: `make_sandbox_executor()` factory

A single constructor used by both the local CLI and (later) the server, mapping superseded agent names to `sbx` agent names.

**Files:**
- Modify: `src/superseded/review/executor.py`
- Test: `tests/test_executor.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_executor.py`:

```python
from superseded.review.executor import make_sandbox_executor


def test_make_sandbox_executor_maps_agent_names(tmp_path):
    ex = make_sandbox_executor(agent_name="claude-code", name="n1", cwd=tmp_path)
    assert isinstance(ex, SandboxExecutor)
    # sbx agent name is resolved at session-build time via _agent_name
    with patch("superseded.review.executor.subprocess.run") as mock_run:
        mock_run.return_value = _completed()
        with ex.session():
            pass
    assert mock_run.call_args_list[0].args[0][4] == "claude"


def test_make_sandbox_executor_unknown_agent_passes_through(tmp_path):
    ex = make_sandbox_executor(agent_name="custom-agent", name="n1", cwd=tmp_path)
    assert isinstance(ex, SandboxExecutor)
    with patch("superseded.review.executor.subprocess.run") as mock_run:
        mock_run.return_value = _completed()
        with ex.session():
            pass
    assert mock_run.call_args_list[0].args[0][4] == "custom-agent"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_executor.py -v`
Expected: FAIL (`make_sandbox_executor` not defined).

- [ ] **Step 3: Implement the factory**

Append to `src/superseded/review/executor.py`:

```python


def make_sandbox_executor(
    *,
    agent_name: str,
    name: str,
    cwd: str | Path | None = None,
    timeout: int = DEFAULT_SANDBOX_TIMEOUT,
    keep_on_error: bool = False,
    binary: str = "sbx",
    io_mode: str = "exec",
) -> SandboxExecutor:
    """Build a SandboxExecutor, mapping superseded agent names to sbx agent names."""
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_executor.py -v`
Expected: PASS (all).

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check src/superseded/review/executor.py tests/test_executor.py && uv run ruff format src/superseded/review/executor.py tests/test_executor.py`

- [ ] **Step 6: Commit**

```bash
git add src/superseded/review/executor.py tests/test_executor.py
git commit -m "feat: add make_sandbox_executor factory with agent-name mapping"
```

---

## Task 7: Route the review engine through sessions

`review()` accepts an optional `executor` (default `SubprocessExecutor()`), opens one session, and the existing `ThreadPoolExecutor` runs each pass as `session.run()`. `run_pass()` takes a `sess` instead of doing inline `subprocess.run`. The existing tests that asserted subprocess behavior move to `test_executor.py` (done in Tasks 3–4); the remaining engine tests are updated here.

**Files:**
- Modify: `src/superseded/review/engine.py`
- Modify: `tests/test_engine.py`

- [ ] **Step 1: Update `tests/test_engine.py`**

In `tests/test_engine.py`:

(a) No new imports are needed in `tests/test_engine.py` (the updated tests below use `MagicMock`/`patch`, already imported). Confirm `from unittest.mock import MagicMock, patch` is present (line 4).

(b) **Delete** these four now-redundant tests (their behavior is covered by `test_executor.py`):
`test_run_pass_raises_on_nonzero_exit`, `test_run_pass_forwards_cwd_to_subprocess`, `test_run_pass_defaults_cwd_to_none`, `test_run_pass_raises_on_timeout`. Also delete the `_make_completed` helper (line 77–78) since nothing in this file uses it after the deletions.

(c) **Replace** `test_run_pass_skips_and_logs_malformed_findings` (lines 143–179) with this version that injects a fake session:

```python
def test_run_pass_skips_and_logs_malformed_findings(caplog):
    import logging

    engine = ReviewEngine(agent=MagicMock(), config=MagicMock())
    raw_items = [
        {
            "severity": "critical",
            "file": "a.py",
            "line": 1,
            "end_line": 2,
            "title": "t",
            "description": "d",
            "suggestion": "s",
            "pass_name": "security",
        },
        {
            "severity": "not-a-severity",
            "file": "b.py",
            "line": 1,
            "end_line": 1,
            "title": "bad",
            "description": "d",
            "suggestion": "s",
            "pass_name": "security",
        },
    ]
    mock_agent = MagicMock()
    mock_agent.build_command.return_value = ["fake"]
    mock_agent.parse_output.return_value = raw_items
    engine.agent = mock_agent

    fake_session = MagicMock()
    fake_session.run.return_value = "x"
    with caplog.at_level(logging.WARNING, logger="superseded.review.engine"):
        findings = engine.run_pass("security", "prompt", sess=fake_session)
    assert len(findings) == 1
    assert findings[0].file == "a.py"
    assert "malformed" in caplog.text.lower() or "not-a-severity" in caplog.text
    fake_session.run.assert_called_once()
```

(d) In `test_review_continues_when_one_pass_fails` (lines 125–140), update the `fake_run_pass` signature from `(pass_name, prompt, timeout=300, progress=None, cwd=None, *, env=None)` to:

```python
    def fake_run_pass(pass_name, prompt, timeout=300, progress=None, sess=None):
        if pass_name == "correctness":
            raise RuntimeError("boom")
        return [good_finding]
```

(e) In `test_review_forwards_conventions_and_spec_signals` (lines 189–214), change the patch target from `superseded.review.engine.subprocess.run` to `superseded.review.executor.subprocess.run`:

```python
    monkeypatch.setattr(
        "superseded.review.executor.subprocess.run",
        lambda *a, **kw: MagicMock(returncode=0, stdout="[]", stderr=""),
    )
```

- [ ] **Step 2: Add a new test asserting executor injection**

Append to `tests/test_engine.py`:

```python
def test_review_uses_injected_executor_session():
    """review() opens exactly one session on the injected executor and runs each pass through it."""
    engine = ReviewEngine(agent=MagicMock(), config=MagicMock(is_pass_enabled=lambda n: True))
    engine.agent.is_available.return_value = True
    engine.agent.build_command.return_value = ["echo"]
    engine.agent.parse_output.return_value = []

    fake_session = MagicMock()
    fake_session.run.return_value = "[]"
    fake_session.__enter__ = MagicMock(return_value=fake_session)
    fake_session.__exit__ = MagicMock(return_value=None)
    fake_executor = MagicMock()
    fake_executor.available.return_value = True
    fake_executor.session.return_value = fake_session

    engine.review(diff="d", passes=["security", "correctness"], executor=fake_executor)

    fake_executor.session.assert_called_once()
    assert fake_session.run.call_count == 2


def test_review_defaults_to_subprocess_executor(monkeypatch):
    """With no executor injected, a SubprocessExecutor is used and its availability checked."""
    monkeypatch.setattr(
        "superseded.review.executor.subprocess.run",
        lambda *a, **kw: MagicMock(returncode=0, stdout="[]", stderr=""),
    )
    agent = MagicMock()
    agent.is_available.return_value = True
    agent.build_command.return_value = ["echo"]
    agent.parse_output.return_value = []
    engine = ReviewEngine(agent=agent, config=MagicMock(is_pass_enabled=lambda n: True))
    engine.review(diff="d", passes=["security"])  # should not raise
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_engine.py -v`
Expected: FAIL (engine still calls `subprocess.run` inline / `run_pass` signature mismatch).

- [ ] **Step 4: Refactor `src/superseded/review/engine.py`**

Add the import (after the existing `from superseded.review.prompts import build_prompt` line):

```python
from superseded.review.executor import AgentExecutor, SubprocessExecutor
```

Replace the `run_pass` method (lines 51–100) with:

```python
    def run_pass(
        self,
        pass_name: str,
        prompt: str,
        timeout: int = DEFAULT_PASS_TIMEOUT,
        progress: ProgressFn | None = None,
        sess=None,
    ) -> list[Finding]:
        if sess is None:
            sess = SubprocessExecutor().session()
        cmd = self.agent.build_command()
        if progress is not None:
            progress(pass_name, "start")
        stdout = sess.run(cmd, prompt, timeout=timeout)
        raw_findings = self.agent.parse_output(stdout, pass_name)
        findings = []
        for item in raw_findings:
            try:
                findings.append(Finding(**item))
            except Exception as err:
                logger.warning("Skipping malformed finding item in pass %s: %s", pass_name, err)
        if progress is not None:
            progress(pass_name, "done")
        return findings
```

In `review()` (lines 102–168):
- Add the `executor` parameter to the signature (after `env: dict[str, str] | None = None,`):

```python
        *,
        env: dict[str, str] | None = None,
        executor: AgentExecutor | None = None,
```

- Replace the availability guard block (lines 120–124) with:

```python
        resolved_executor = executor if executor is not None else SubprocessExecutor()
        if not resolved_executor.available(self.agent):
            raise RuntimeError(
                f"Agent CLI '{self.agent.name}' not found on PATH. "
                "Install it or choose a different agent with --agent."
            )
```

- Wrap the thread pool in a session. Replace lines 132–153 (the `all_findings`/`warnings` setup and the `with ThreadPoolExecutor(...)` block) with:

```python
        all_findings: list[list[Finding]] = []
        warnings: list[str] = []

        with resolved_executor.session(cwd, env=env) as sess:
            with ThreadPoolExecutor(max_workers=max(1, len(passes))) as pool:
                future_to_pass = {}
                for pass_name in passes:
                    prompt = build_prompt(
                        pass_name=pass_name,
                        diff=diff,
                        pr_description=pr_description,
                        file_context=file_context,
                        memory_context=memory_context,
                        static_signals=static_signals,
                        usage_signals=usage_signals,
                        conventions_signals=conventions_signals,
                        spec_signals=spec_signals,
                        learned_context=learned_context,
                    )
                    future = pool.submit(self.run_pass, pass_name, prompt, timeout, progress, sess)
                    future_to_pass[future] = pass_name

                for future in as_completed(future_to_pass):
                    pass_name = future_to_pass[future]
                    try:
                        all_findings.append(future.result())
                    except Exception as err:
                        msg = f"Review pass '{pass_name}' failed and was skipped: {err}"
                        logger.warning(msg)
                        warnings.append(msg)
                        if progress is not None:
                            progress(pass_name, "failed")
```

- [ ] **Step 5: Run the engine tests to verify they pass**

Run: `uv run pytest tests/test_engine.py -v`
Expected: PASS (all).

- [ ] **Step 6: Run the full suite to check for collateral breakage**

Run: `uv run pytest tests/ -q`
Expected: PASS (any failures here are pre-existing tests referencing the old `run_pass` signature — fix in Task 9; note them but continue).

- [ ] **Step 7: Lint and format**

Run: `uv run ruff check src/superseded/review/engine.py tests/test_engine.py && uv run ruff format src/superseded/review/engine.py tests/test_engine.py`

- [ ] **Step 8: Commit**

```bash
git add src/superseded/review/engine.py tests/test_engine.py
git commit -m "refactor: route review engine through executor sessions"
```

---

## Task 8: Add `sandbox` to local `Config`

**Files:**
- Modify: `src/superseded/config.py`
- Test: `tests/test_config.py` (create if absent)

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py` (or append if it exists):

```python
from __future__ import annotations

from superseded.config import Config


def test_config_sandbox_defaults_false():
    assert Config().sandbox is False


def test_config_sandbox_roundtrips(tmp_path):
    from superseded.config import load_config, write_config

    cfg = Config(sandbox=True)
    path = tmp_path / ".superseded.yaml"
    write_config(cfg, path)
    loaded = load_config(path)
    assert loaded.sandbox is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL (`sandbox` attribute missing / `AttributeError`).

- [ ] **Step 3: Add the field**

In `src/superseded/config.py`, inside `class Config(BaseModel):`, add `sandbox: bool = False` immediately after the `graph: bool = True` line (line 31):

```python
    graph: bool = True
    sandbox: bool = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check src/superseded/config.py tests/test_config.py && uv run ruff format src/superseded/config.py tests/test_config.py`

- [ ] **Step 6: Commit**

```bash
git add src/superseded/config.py tests/test_config.py
git commit -m "feat: add sandbox toggle to Config"
```

---

## Task 9: Local CLI `--sandbox` toggle + executor selection

Add `SUPERSEDED_SANDBOX` env + `--sandbox/--no-sandbox` flag (precedence: env > flag > config > default False), build the executor, and pass it to `engine.review`. Also fix the `fake_run_pass` signature in `test_cli.py` (broken by Task 7).

**Files:**
- Modify: `src/superseded/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Update `tests/test_cli.py` — fix `fake_run_pass` and add sandbox tests**

(a) In `test_run_review_honors_config_disabled_passes_when_flag_omitted` (line 226), change the fake's signature to accept `sess`:

```python
    def fake_run_pass(self, pass_name, prompt, timeout=300, progress=None, sess=None):
        invoked.append(pass_name)
        if progress is not None:
            progress(pass_name, "done")
        return []
```

(b) Append the sandbox tests to `tests/test_cli.py`:

```python
def test_resolve_sandbox_env_overrides_flag(monkeypatch):
    from superseded.cli import resolve_sandbox

    with monkeypatch.context() as m:
        m.setenv("SUPERSEDED_SANDBOX", "0")
        assert resolve_sandbox(True, Config()) is False


def test_resolve_sandbox_env_truthy_overrides_flag(monkeypatch):
    from superseded.cli import resolve_sandbox

    with monkeypatch.context() as m:
        m.setenv("SUPERSEDED_SANDBOX", "1")
        assert resolve_sandbox(False, Config()) is True


def test_resolve_sandbox_flag_overrides_config():
    from superseded.cli import resolve_sandbox

    assert resolve_sandbox(True, Config(sandbox=False)) is True
    assert resolve_sandbox(False, Config(sandbox=True)) is False


def test_resolve_sandbox_defaults_to_config():
    from superseded.cli import resolve_sandbox

    assert resolve_sandbox(None, Config(sandbox=True)) is True
    assert resolve_sandbox(None, Config(sandbox=False)) is False


def test_resolve_sandbox_defaults_false():
    from superseded.cli import resolve_sandbox

    assert resolve_sandbox(None, Config()) is False


def test_run_review_sandbox_missing_sbx_exits(tmp_path, monkeypatch, capsys):
    """--sandbox with no sbx on PATH exits 2 with a clear sbx message."""
    monkeypatch.setattr(
        "superseded.cli.fetch_diff",
        lambda pr=None, diff_range=None, files=None, staged=False: "diff --git a/x.py b/x.py\n",
    )
    monkeypatch.setattr("superseded.cli.fetch_pr_description", lambda pr: None)
    monkeypatch.setattr("superseded.context.gathering.compute_file_context", lambda diff, root=None: None)
    monkeypatch.setattr("superseded.cli.repo_root", lambda: tmp_path)
    monkeypatch.setattr("superseded.context.gathering.run_static_analysis", lambda files, root: None)
    monkeypatch.setattr("superseded.context.gathering.retrieve_usages", lambda diff, root: None)
    monkeypatch.setattr("superseded.cli.current_repo", lambda: None)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/claude" if cmd != "sbx" else None)

    from superseded.cli import _run_review

    with pytest.raises(SystemExit) as exc:
        _run_review(
            pr=None,
            diff_range="HEAD~1..HEAD",
            agent=None,
            model=None,
            output_format="json",
            post=False,
            passes=None,
            sandbox=True,
        )
    assert exc.value.code == 2
    assert "sbx" in capsys.readouterr().err.lower()


def test_run_review_sandbox_builds_sandbox_executor(tmp_path, monkeypatch):
    """--sandbox with sbx present builds a SandboxExecutor and passes it to engine.review."""
    monkeypatch.setattr(
        "superseded.cli.fetch_diff",
        lambda pr=None, diff_range=None, files=None, staged=False: "diff --git a/x.py b/x.py\n",
    )
    monkeypatch.setattr("superseded.cli.fetch_pr_description", lambda pr: None)
    monkeypatch.setattr("superseded.context.gathering.compute_file_context", lambda diff, root=None: None)
    monkeypatch.setattr("superseded.cli.repo_root", lambda: tmp_path)
    monkeypatch.setattr("superseded.context.gathering.run_static_analysis", lambda files, root: None)
    monkeypatch.setattr("superseded.context.gathering.retrieve_usages", lambda diff, root: None)
    monkeypatch.setattr("superseded.cli.current_repo", lambda: None)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/sbx")

    captured: dict = {}

    def fake_review(self, **kwargs):
        captured.update(kwargs)
        from superseded.models import ReviewResult

        return ReviewResult(findings=[], warnings=[])

    monkeypatch.setattr("superseded.review.engine.ReviewEngine.review", fake_review)
    monkeypatch.setattr("superseded.review.engine.ReviewEngine.run_pass", lambda self, *a, **k: [])

    from superseded.cli import _run_review
    from superseded.review.executor import SandboxExecutor

    _run_review(
        pr=None,
        diff_range="HEAD~1..HEAD",
        agent=None,
        model=None,
        output_format="json",
        post=False,
        passes=None,
        sandbox=True,
    )
    ex = captured.get("executor")
    assert isinstance(ex, SandboxExecutor)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL (`resolve_sandbox` / `sandbox` kwarg not defined).

- [ ] **Step 3: Implement the CLI changes in `src/superseded/cli.py`**

(a) Add imports near the top of the file. After `import subprocess` (line 7) add `import uuid`. After the `from superseded.review.engine import ReviewEngine` import (line 42), add:

```python
from superseded.review.executor import SubprocessExecutor, make_sandbox_executor
```

(b) Add the env constant after `GRAPH_ENV = "SUPERSEDED_GRAPH"` (line 46):

```python
SANDBOX_ENV = "SUPERSEDED_SANDBOX"
```

(c) Add `resolve_sandbox` immediately after `resolve_graph` (after line 83):

```python
def resolve_sandbox(cli_value: bool | None, config: Config) -> bool:
    env = os.environ.get(SANDBOX_ENV)
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    if cli_value is not None:
        return cli_value
    return config.sandbox
```

(d) Add `_select_executor` after `resolve_sandbox`:

```python
def _select_executor(sandbox: bool, *, agent_name: str, timeout: int):
    if not sandbox:
        return SubprocessExecutor()
    return make_sandbox_executor(
        agent_name=agent_name,
        name=f"superseded-local-{uuid.uuid4().hex[:10]}",
        timeout=timeout,
    )
```

(e) Add the `--sandbox/--no-sandbox` option to the `review` command. After the `--graph/--no-graph` option block (lines 269–274), add:

```python
@click.option(
    "--sandbox/--no-sandbox",
    "sandbox",
    default=None,
    help="Run agents inside an sbx Docker Sandbox (default: from config; env SUPERSEDED_SANDBOX).",
)
```

(f) Add `sandbox: bool | None` to the `review()` function signature (after `graph: bool | None,` around line 294) and pass it through to `_run_review`. The `_run_review(...)` call in `review()` (lines 321–340) gains `sandbox=sandbox,`:

```python
    _run_review(
        pr=pr,
        diff_range=diff_range,
        agent=agent,
        model=model,
        output_format=output_format,
        post=post,
        passes=pass_list,
        timeout=timeout,
        config_path=config_path,
        no_memory=no_memory,
        full=full_review,
        no_static=no_static,
        no_usage=no_usage,
        no_conventions=no_conventions,
        no_specs=no_specs,
        graph=graph,
        sandbox=sandbox,
        staged=staged,
        files=list(files) or None,
    )
```

(g) Add `sandbox: bool | None = None,` to the `_run_review` signature (after `graph: bool | None = None,` around line 360).

(h) In `_run_review`, replace the agent-availability block (lines 372–383):

```python
    try:
        engine = ReviewEngine.select(agent_name, model=model_name, config=config)
    except ValueError as err:
        click.echo(f"Error: {err}", err=True)
        sys.exit(2)
    if not engine.agent.is_available():
        click.echo(
            f"Error: Agent CLI '{engine.agent.name}' not found on PATH. "
            "Install it or choose a different agent with --agent.",
            err=True,
        )
        sys.exit(2)
```

with:

```python
    try:
        engine = ReviewEngine.select(agent_name, model=model_name, config=config)
    except ValueError as err:
        click.echo(f"Error: {err}", err=True)
        sys.exit(2)

    pass_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
    sandbox = resolve_sandbox(sandbox, config)
    executor = _select_executor(sandbox, agent_name=agent_name, timeout=pass_timeout)
    if not executor.available(engine.agent):
        click.echo(
            (
                "Error: 'sbx' (Docker Sandboxes) not found on PATH. "
                "Install docker-sbx to use --sandbox."
                if sandbox
                else f"Error: Agent CLI '{engine.agent.name}' not found on PATH. "
                "Install it or choose a different agent with --agent."
            ),
            err=True,
        )
        sys.exit(2)
```

(i) Later in `_run_review`, the existing `pass_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT` line (line 463) is now duplicated — **delete** that line (it was moved up in step h). Then add `executor=executor` to the `engine.review(...)` call. The call (lines 466–480) becomes:

```python
    try:
        result = engine.review(
            diff=diff,
            pr_description=pr_description,
            file_context=file_context,
            memory_context=memory_context,
            static_signals=static_signals,
            usage_signals=usage_signals,
            conventions_signals=conventions_signals,
            spec_signals=spec_signals,
            learned_context=learned_context,
            passes=passes,
            timeout=pass_timeout,
            progress=_progress,
            cwd=str(root),
            executor=executor,
        )
    except RuntimeError as err:
        click.echo(f"Error: {err}", err=True)
        sys.exit(1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS (all, including the new sandbox tests and the fixed `fake_run_pass`).

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check src/superseded/cli.py tests/test_cli.py && uv run ruff format src/superseded/cli.py tests/test_cli.py`

- [ ] **Step 6: Commit**

```bash
git add src/superseded/cli.py tests/test_cli.py
git commit -m "feat: add --sandbox toggle and executor selection to CLI"
```

---

## Task 10: Document `sbx` dependency and executor toggle in AGENTS.md

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Update the runtime-deps paragraph**

In `AGENTS.md`, find the paragraph beginning "Runtime external dependencies an agent typically will not have" (under Architecture notes) and append a sentence:

```markdown
- Agent execution has a pluggable backend (`review/executor.py`): `SubprocessExecutor` shells out to the AI CLI directly (default for `superseded review`); `SandboxExecutor` runs it inside an `sbx` Docker Sandbox microVM. Toggle locally with `--sandbox`/`--no-sandbox` or `SUPERSEDED_SANDBOX` (env > flag > `.superseded.yaml` `sandbox` > default false). `SandboxExecutor` requires the `sbx` CLI on PATH (package `docker-sbx`) plus a KVM-capable host; tests mock `subprocess.run`/`shutil.which`, never invoking real `sbx`.
```

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs: document sbx dependency and executor toggle"
```

---

## Task 11: Full verification

- [ ] **Step 1: Run the complete test suite**

Run: `uv run pytest tests/ -q`
Expected: PASS (all). If any test still references the old `run_pass(self, pass_name, prompt, timeout=300, progress=None, cwd=None, *, env=None)` signature, update it to `(..., sess=None)` — search: `rg -n "cwd=None, \*, env=None" tests/`.

- [ ] **Step 2: Lint and format the whole project**

Run: `uv run ruff check src/ tests/ && uv run ruff format src/ tests/`
Expected: clean.

- [ ] **Step 3: Smoke-test the CLI help shows the new flag**

Run: `uv run superseded review --help`
Expected: output contains `--sandbox/--no-sandbox`.

- [ ] **Step 4: Final commit (if formatting changed anything)**

```bash
git add -A
git commit -m "test: full suite green for sandbox executor foundation"
```

---

## Self-review notes

- **Spec coverage:** The executor abstraction, engine routing, local `--sandbox` toggle, and both I/O modes are all implemented and tested. The server `/review/pr` endpoint, installation resolution, worker sandbox wiring, `ServerConfig` fields, `action.yml` rewrite, and `compose.yml` change are deliberately deferred to the follow-up server plan (Plan 2).
- **No placeholders:** every code step contains complete, runnable code; the only manual step (Task 1) is an optional investigation whose outcome changes a default value, not a code path.
- **Type consistency:** `Session.run(cmd, prompt, *, timeout) -> str` and `AgentExecutor.available(agent)/session(cwd, *, env)` are used identically in executor.py, engine.py, and cli.py. `run_pass(..., sess=None)` matches the `fake_run_pass(..., sess=None)` updates in both test files. `make_sandbox_executor(*, agent_name, name, cwd=None, timeout, ...)` matches its call site in `_select_executor`.
