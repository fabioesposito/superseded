from __future__ import annotations

import contextlib
import importlib.util
import logging
import os
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

SBX_AGENT_MAP: dict[str, str] = {
    "claude-code": "claude",
    "opencode": "opencode",
    "codex": "codex",
}


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


class _SandboxSession:
    """One sbx microVM, shared across the concurrent passes of a single review.

    ``run()`` spawns an independent ``sbx exec`` host subprocess per call, so it
    is safe to invoke concurrently from multiple threads (the engine runs all
    passes against one session). ``_errored`` is a monotonic latch (only ever
    set False→True) read by ``__exit__`` after the pass pool has joined, so the
    shared-session model does not race.
    """

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
        prompt_path = Path(self._cwd) / f".sbx_prompt_{uuid.uuid4().hex[:8]}.txt"
        try:
            prompt_path.write_text(prompt)
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


class SandboxExecutor:
    """Runs agent CLIs inside an `sbx` microVM sandbox (one per session).

    Provider credentials are injected into the sandbox by `sbx`'s host proxy
    (via `sbx secret set`), so the `env` passed to ``session()`` is intentionally
    NOT forwarded into the microVM.
    """

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


_DEFAULT_PROVIDER_KEYS: dict[str, str] = {
    "ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY": "OPENAI_API_KEY",
}

SMOLVM_AVAILABLE = importlib.util.find_spec("smol") is not None


def _smol() -> tuple[type, type, type, type, type]:
    """Lazily import smol types; raise AgentRunError if the extra isn't installed."""
    try:
        from smol import ExecOptions, Machine, MachineConfig, MountSpec, ResourceSpec
    except ImportError as err:
        raise AgentRunError(
            "smolmachines extra not installed; run `uv sync --extra sandbox` "
            "to enable smolvm sandbox mode."
        ) from err
    return Machine, MachineConfig, MountSpec, ResourceSpec, ExecOptions


def _filter_provider_keys(mapping: dict[str, str], environ: dict[str, str]) -> dict[str, str]:
    """Return the {guest_env: host_value} subset whose host env var is actually set."""
    return {guest: environ[host] for guest, host in mapping.items() if host in environ}


class _SmolvmSession:
    """One smolvm machine, shared across the concurrent passes of a review."""

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
        self._machine = None
        self._errored = False

    def __enter__(self) -> _SmolvmSession:
        machine_cls, machine_cfg_cls, mount_spec_cls, resource_spec_cls, _ = _smol()
        try:
            self._machine = machine_cls.create(
                machine_cfg_cls(
                    name=self._name,
                    image=self._image,
                    mounts=[mount_spec_cls(source=self._cwd, target="/workspace", read_only=False)],
                    resources=resource_spec_cls(network=True),
                )
            )
        except Exception as err:
            raise AgentRunError(f"smol Machine.create failed: {err}") from err
        return self

    def __exit__(self, *exc: object) -> None:
        if self._keep_on_error and self._errored:
            logger.warning("keep_on_error: leaving smolvm %s for inspection", self._name)
            return None
        try:
            if self._machine is not None:
                self._machine.delete()
        except Exception:
            logger.warning("smol delete failed for machine %s", self._name)
        return None

    def run(self, cmd: list[str], prompt: str, *, timeout: int) -> str:
        _, _, _, _, exec_options_cls = _smol()
        prompt_guest = f"/tmp/_smol_prompt_{uuid.uuid4().hex[:8]}.txt"
        shell = f"cd /workspace && {shlex.join(cmd)} < {shlex.quote(prompt_guest)}"
        try:
            self._machine.write_file(prompt_guest, prompt)
            result = self._machine.exec(
                ["sh", "-c", shell],
                exec_options_cls(env=dict(self._keys), workdir="/workspace", timeout=timeout),
            )
        except Exception as err:
            self._errored = True
            raise AgentRunError(f"smol exec failed: {err}") from err
        if result.exit_code != 0:
            self._errored = True
            stderr = result.stderr.strip()
            raise AgentRunError(
                f"Agent '{cmd[0]}' exited {result.exit_code}" + (f": {stderr}" if stderr else "")
            )
        return result.stdout


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
                "SmolvmExecutor requires a cwd (the repo checkout) for the machine workspace mount."
            )
        return _SmolvmSession(
            image=self._image,
            name=self._name,
            cwd=str(resolved),
            timeout=self._timeout,
            keep_on_error=self._keep_on_error,
            keys=_filter_provider_keys(self._keys, os.environ),
        )
