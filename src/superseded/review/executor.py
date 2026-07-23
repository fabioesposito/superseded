from __future__ import annotations

import contextlib
import importlib.util
import logging
import os
import posixpath
import shlex
import shutil
import subprocess
import tempfile
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


def _seed_agent_auth(agent_name: str, xdg_data_home: Path) -> None:
    """Copy agent auth files from the host into an isolated XDG_DATA_HOME.

    Only opencode's auth.json lives under XDG_DATA_HOME (and holds real
    provider keys). claude-code and codex store auth at paths relative to
    HOME, which is not redirected, so they need no seeding here.
    """
    if agent_name != "opencode":
        return
    host_data = Path(
        os.environ.get(
            "XDG_DATA_HOME",
            os.path.join(os.path.expanduser("~"), ".local", "share"),
        )
    )
    host_auth = host_data / "opencode" / "auth.json"
    if not host_auth.is_file():
        return
    target = xdg_data_home / "opencode" / "auth.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(host_auth.read_text())


class _SubprocessSession:
    def __init__(
        self,
        cwd: str | None,
        env: dict[str, str] | None,
        agent_name: str | None = None,
    ) -> None:
        self._cwd = cwd
        self._env = env
        self._agent_name = agent_name

    def __enter__(self) -> _SubprocessSession:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def _build_isolated_env(self, base: Path) -> dict[str, str]:
        """Build an env dict with per-exec isolated XDG directories.

        When multiple agent subprocesses share ``$XDG_DATA_HOME`` (the default
        for the local subprocess path), their SQLite state DBs collide and error
        with "database is locked" — opencode is especially vulnerable because it
        touches several SQLite DBs (state, cache, data) concurrently.

        Redirecting only the XDG directories keeps ``HOME`` intact so the agent
        can reach ``~/.gitconfig`` and ``~/.ssh``."""
        xdg_data = base / "data"
        xdg_cache = base / "cache"
        xdg_state = base / "state"
        for d in (xdg_data, xdg_cache, xdg_state):
            d.mkdir(parents=True, exist_ok=True)

        env = dict(self._env) if self._env is not None else os.environ.copy()
        env["XDG_DATA_HOME"] = str(xdg_data)
        env["XDG_CACHE_HOME"] = str(xdg_cache)
        env["XDG_STATE_HOME"] = str(xdg_state)
        if self._agent_name:
            _seed_agent_auth(self._agent_name, xdg_data)
        return env

    def run(self, cmd: list[str], prompt: str, *, timeout: int) -> str:
        if self._agent_name:
            with tempfile.TemporaryDirectory(prefix=f"superseded-{self._agent_name}-") as tmp:
                env = self._build_isolated_env(Path(tmp))
                return self._run(cmd, prompt, timeout, env)
        return self._run(cmd, prompt, timeout, self._env)

    def _run(
        self,
        cmd: list[str],
        prompt: str,
        timeout: int,
        env: dict[str, str] | None,
    ) -> str:
        try:
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self._cwd,
                env=env,
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

    def __init__(self, agent_name: str | None = None) -> None:
        self._agent_name = agent_name

    def available(self, agent: Agent) -> bool:
        return agent.is_available()

    def session(
        self, cwd: str | Path | None = None, *, env: dict[str, str] | None = None
    ) -> Session:
        return _SubprocessSession(
            str(cwd) if cwd is not None else None, env, agent_name=self._agent_name
        )


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
            # Create 0600 from the outset: the prompt embeds diff/file context
            # that may contain secrets; the default umask would leave it 0644.
            fd = os.open(prompt_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(prompt)
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
    provider_files: dict[str, str] | None = None,
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
            provider_files=provider_files
            if provider_files is not None
            else agent_credential_files(agent_name),
            smolvm_binary=smolvm_binary,
        )
    raise ValueError(f"unknown sandbox kind: {kind!r}")


_DEFAULT_PROVIDER_KEYS: dict[str, str] = {
    "ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY": "OPENAI_API_KEY",
}


def agent_credential_files(agent_name: str) -> dict[str, str]:
    """Return ``{guest_path: content}`` for the host credential files an agent
    CLI reads from ``$HOME``, so they can be materialized inside the sandbox VM.

    Only files that exist on the host are returned. Guest paths are anchored
    under ``/root`` so the per-exec HOME relocation (``_relocate_under_root``)
    rewrites them into each pass's isolated guest HOME.

    Auth caveats (the env-key injection via ``_DEFAULT_PROVIDER_KEYS`` is the
    reliable fallback; this file-seeding is additive):
      * opencode     - ``$XDG_DATA_HOME/opencode/auth.json`` holds the real
                       provider keys; seeds cleanly.
      * claude-code  - ``~/.claude.json`` carries UI prefs/userID only; the OAuth
                       token lives in the OS keychain, so this seeds the file but
                       does NOT by itself authenticate (use ``ANTHROPIC_API_KEY``).
      * codex        - ``~/.codex/auth.json`` only exists after ``codex login``;
                       absent until then (use ``OPENAI_API_KEY`` / ``CODEX_API_KEY``).
    """
    home = os.path.expanduser("~")
    data_home = os.environ.get("XDG_DATA_HOME", os.path.join(home, ".local", "share"))
    candidates: dict[str, str] = {}  # guest_path -> host_path
    if agent_name == "opencode":
        candidates["/root/.local/share/opencode/auth.json"] = os.path.join(
            data_home, "opencode", "auth.json"
        )
    elif agent_name == "claude-code":
        candidates["/root/.claude.json"] = os.path.join(home, ".claude.json")
    elif agent_name == "codex":
        candidates["/root/.codex/auth.json"] = os.path.join(home, ".codex", "auth.json")
    out: dict[str, str] = {}
    for guest_path, host_path in candidates.items():
        if os.path.isfile(host_path):
            with contextlib.suppress(OSError):
                out[guest_path] = Path(host_path).read_text()
    return out


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


def _format_env_file(env: dict[str, str]) -> str:
    """Render env vars as POSIX ``export KEY='value'`` lines for sourcing.

    Used to pass provider keys (and HOME) into a smolvm guest *without* putting
    them on the ``smolvm machine exec`` argv, which is world-readable via
    ``ps``/``/proc/<pid>/cmdline``. Single quotes are escaped so values round-trip
    through ``. <file>`` in any POSIX sh.
    """
    lines = []
    for key, value in env.items():
        escaped = value.replace("'", "'\\''")
        lines.append(f"export {key}='{escaped}'")
    return "\n".join(lines)


_AGENT_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
    }
)


def build_agent_env(environ: dict[str, str]) -> dict[str, str]:
    """Build an explicit allowlist env for an agent subprocess.

    Only what an AI CLI needs to run and authenticate is forwarded (``PATH``/
    ``HOME``/``XDG_*``, locale, and the provider API keys). Everything else from
    the server process environment — including unrelated operator secrets such
    as ``AWS_*``, ``GITHUB_TOKEN`` or cloud creds — is dropped, avoiding the
    deny-list model (``not SUPERSEDED_*``) that fails open for any future secret.
    ``GIT_TERMINAL_PROMPT=0`` keeps the agent's own git calls from blocking on an
    interactive credential prompt when run against untrusted checkout contents.
    """
    env = {k: v for k, v in environ.items() if k in _AGENT_ENV_ALLOWLIST}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _relocate_under_root(guest_path: str, new_root: str) -> str:
    """Rewrite a /root-prefixed guest path to live under ``new_root``.

    auth files are seeded keyed to /root/.local/...; under per-exec HOME
    isolation they must move to <new_root>/.local/... so opencode finds them.
    Paths not starting with /root are returned unchanged.
    """
    if guest_path == "/root":
        return new_root
    if guest_path.startswith("/root/"):
        return new_root + guest_path[len("/root") :]
    return guest_path


def _resolve_boot_source(image: str, smolvm_binary: str) -> str:
    """Decide how to materialize ``image`` into a smolvm machine.

    Returns one of:
      * ``"file"``     - image is a path to a docker-save/rootfs archive on disk
                        (passed straight to ``smolvm ... --image <path>``).
      * ``"docker"``   - image is a bare LOCAL docker tag; we stream
                        ``docker save <tag>`` into ``smolvm ... --image -`` so a
                        locally-built image works with no tar file and no push.
      * ``"registry"`` - anything else (e.g. ``ghcr.io/org/img:tag``): the
                        embedded smol SDK pulls it at boot.
    """
    if not image:
        return "registry"
    if os.path.exists(image):
        return "file"
    if shutil.which("docker"):
        try:
            inspect = subprocess.run(
                ["docker", "image", "inspect", image],
                capture_output=True,
                timeout=30,
            )
        except Exception:
            return "registry"
        if inspect.returncode == 0:
            return "docker"
    return "registry"


def _docker_image_id(image: str) -> str | None:
    """Fast content-addressable key for a local docker tag (no ``docker save``)."""
    try:
        out = subprocess.run(
            ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _docker_cache_dir() -> Path:
    return Path(
        os.environ.get("SUPERSEDED_SMOLVM_IMAGE_CACHE")
        or os.path.expanduser("~/.cache/superseded/smolvm-images")
    )


def _resolve_docker_cache(image: str) -> str | None:
    """Return a cached local-tar path for a local docker ``image``.

    Promotes the slow ``docker`` boot source (which streams ``docker save`` into
    smolvm on every review, ~3-4s of host I/O) to the fast ``file`` boot source
    (~0.5s create) by materializing the archive once into a content-addressed
    cache keyed on the image ID. Subsequent reviews with the same image ID hit
    the cache and skip ``docker save`` entirely; rebuilding the image changes
    its ID and triggers a single re-save.

    Returns ``None`` on any failure (docker missing, save error, FS error); the
    caller falls back to the streaming path unchanged.
    """
    image_id = _docker_image_id(image)
    if not image_id:
        return None
    cache_dir = _docker_cache_dir()
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return None
    digest = image_id.rsplit(":", 1)[-1] if ":" in image_id else image_id
    target = cache_dir / f"{digest[:24]}.tar"
    if target.exists() and target.stat().st_size > 0:
        logger.info("smolvm image cache hit for %s -> %s", image, target)
        return str(target)
    # Materialize atomically: save to a sibling temp file, then rename into place
    # so a partial cache file is never visible to a concurrent review.
    staging = target.with_suffix(".tar.part")
    try:
        save = subprocess.run(
            ["docker", "save", image, "-o", str(staging)],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except Exception:
        with contextlib.suppress(Exception):
            staging.unlink(missing_ok=True)
        return None
    if save.returncode != 0:
        with contextlib.suppress(Exception):
            staging.unlink(missing_ok=True)
        logger.warning(
            "smolvm image cache: docker save failed for %s; falling back to stream",
            image,
        )
        return None
    try:
        os.replace(staging, target)
    except Exception:
        with contextlib.suppress(Exception):
            staging.unlink(missing_ok=True)
        return None
    logger.info("smolvm image cache populated for %s -> %s", image, target)
    return str(target)


class _SmolvmSession:
    """One smolvm machine, shared across the concurrent passes of a review.

    Two boot modes, selected automatically from the image reference:
      * local file (a docker-save archive / rootfs tar/dir on disk) → shell out to
        the ``smolvm`` CLI, which supports local image archives; the embedded SDK
        does not (it only pulls registry refs).
      * registry reference (e.g. ``ghcr.io/org/img:tag``) → embedded ``smol`` SDK.
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
        auth_files: dict[str, str] | None = None,
        smolvm_binary: str = "smolvm",
    ) -> None:
        self._image = image
        self._name = name
        self._cwd = cwd
        self._timeout = timeout
        self._keep_on_error = keep_on_error
        self._keys = keys
        self._auth_files = auth_files or {}
        self._smolvm_binary = smolvm_binary
        self._machine = None
        self._errored = False
        # Boot source: how the image reaches the VM.
        #  - "file":     image is a path to a docker-save/rootfs archive on disk
        #  - "docker":   image is a bare local docker tag → pipe `docker save`
        #                into `smolvm ... --image -` (no tar file, no push/pull)
        #  - "registry": image is a registry ref → embedded smol SDK pulls it
        self._boot_source = _resolve_boot_source(image, smolvm_binary)
        # Promote docker→file via a content-addressed cache so repeated reviews
        # skip the ~3-4s `docker save` stream and boot from the local tar
        # (~0.5s create). Falls back silently to streaming on any failure.
        if self._boot_source == "docker":
            cached = _resolve_docker_cache(image)
            if cached is not None:
                self._image = cached
                self._boot_source = "file"

    @property
    def _cli_mode(self) -> bool:
        return self._boot_source in ("file", "docker")

    # -- lifecycle ----------------------------------------------------------
    def __enter__(self) -> _SmolvmSession:
        if self._cli_mode:
            self._cli_start()
            # CLI mode seeds auth per-exec under an isolated HOME (see _cli_run),
            # so there is nothing to seed at session start.
        else:
            self._sdk_start()
            for guest_path, content in self._auth_files.items():
                self._seed_file_sdk(guest_path, content)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._keep_on_error and self._errored:
            logger.warning("keep_on_error: leaving smolvm %s for inspection", self._name)
            return None
        try:
            if self._cli_mode:
                subprocess.run(
                    [self._smolvm_binary, "machine", "delete", "--name", self._name, "-f"],
                    check=False,
                    capture_output=True,
                    timeout=max(30, self._timeout),
                )
            elif self._machine is not None:
                self._machine.delete()
        except Exception:
            logger.warning("smol delete failed for machine %s", self._name)
        return None

    # -- run ----------------------------------------------------------------
    def run(self, cmd: list[str], prompt: str, *, timeout: int) -> str:
        # CLI mode persists files across execs under /root; the VM's /tmp is
        # ephemeral per exec there. SDK mode runs in one continuous VM where
        # /tmp is stable, so it keeps the historic /tmp location.
        prefix = "/root" if self._cli_mode else "/tmp"
        prompt_guest = f"{prefix}/_smol_prompt_{uuid.uuid4().hex[:8]}.txt"
        if self._cli_mode:
            return self._cli_run(cmd, prompt, prompt_guest, timeout=timeout)
        return self._sdk_run(cmd, prompt, prompt_guest, timeout=timeout)

    # -- CLI mode -----------------------------------------------------------
    def _cli(self, *args: str, **run_kw) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self._smolvm_binary, *args],
            capture_output=True,
            text=True,
            **run_kw,
        )

    def _cli_start(self) -> None:
        create_argv = [
            "machine",
            "create",
            "--name",
            self._name,
            "--net",
            "-v",
            f"{self._cwd}:/workspace",
        ]
        if self._boot_source == "docker":
            # Stream `docker save <tag>` into smolvm via stdin (`--image -`): a
            # locally-built image boots with no tar file on disk and no push/pull.
            docker = subprocess.Popen(
                ["docker", "save", self._image],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                create = subprocess.run(
                    [self._smolvm_binary, *create_argv, "--image", "-"],
                    stdin=docker.stdout,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
            finally:
                if docker.stdout:
                    docker.stdout.close()
                docker.wait(timeout=120)
            if create.returncode != 0:
                raise AgentRunError(
                    f"smolvm create failed: {(create.stderr or create.stdout).strip()}"
                )
        else:
            create = self._cli(*create_argv, "--image", self._image, timeout=300)
            if create.returncode != 0:
                raise AgentRunError(
                    f"smolvm create failed: {(create.stderr or create.stdout).strip()}"
                )
        start = self._cli("machine", "start", "--name", self._name, timeout=180)
        if start.returncode != 0:
            raise AgentRunError(f"smolvm start failed: {(start.stderr or start.stdout).strip()}")

    def _seed_file_sdk(self, guest_path: str, content: str) -> None:
        guest_dir = posixpath.dirname(guest_path)
        try:
            _, _, _, _, exec_options_cls = _smol()
            if guest_dir:
                self._machine.exec(["mkdir", "-p", guest_dir], exec_options_cls())
            self._machine.write_file(guest_path, content)
        except Exception as err:
            logger.warning("smolvm: failed to seed %s: %s", guest_path, err)

    def _cli_run(self, cmd: list[str], prompt: str, prompt_guest: str, *, timeout: int) -> str:
        argv = [self._smolvm_binary, "machine", "exec", "--name", self._name, "-w", "/workspace"]
        # Passes run concurrently against one shared VM. opencode touches several
        # SQLite DBs (state, cache, data) that error with "database is locked"
        # when two invocations share them, so give each exec a fully isolated
        # HOME and re-seed auth.json into it. /workspace (the repo checkout) is
        # the only shared, read-only-ish path; everything writable is per-exec.
        suffix = uuid.uuid4().hex[:8]
        home = f"/root/.home-{suffix}"
        env = dict(self._keys)
        env.setdefault("HOME", home)
        # Relocate auth files (keyed under /root/...) into the per-exec HOME.
        for guest_path, content in self._auth_files.items():
            target = _relocate_under_root(guest_path, home)
            self._cli_write_guest_file(target, content)
        # Provider keys (and HOME) are written to a guest-side env file under the
        # per-exec HOME and sourced by the shell — they must NOT be passed as
        # ``-e KEY=val`` argv elements, which are visible via ps/proc.
        env_path = f"{home}/.env"
        self._cli_write_guest_file(env_path, _format_env_file(env))
        shell = f". {shlex.quote(env_path)} && {shlex.join(cmd)} < {shlex.quote(prompt_guest)}"
        argv += ["--timeout", f"{timeout}s", "--", "sh", "-c", shell]
        try:
            self._cli_write_guest_file(prompt_guest, prompt)
            result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout + 30)
        except subprocess.TimeoutExpired as err:
            self._errored = True
            raise AgentRunError(f"smolvm exec timed out after {timeout}s") from err
        except Exception as err:
            self._errored = True
            raise AgentRunError(f"smolvm exec failed: {err}") from err
        if result.returncode != 0:
            self._errored = True
            stderr = result.stderr.strip()
            raise AgentRunError(
                f"Agent '{cmd[0]}' exited {result.returncode}" + (f": {stderr}" if stderr else "")
            )
        return result.stdout

    def _cli_write_guest_file(self, guest_path: str, content: str) -> None:
        """Copy host-written bytes to a guest path via `smolvm machine cp`.

        Creates the parent directory first. Best-effort: failures are logged
        but not fatal (a missing auth file surfaces as an auth error downstream).
        """
        guest_dir = posixpath.dirname(guest_path)
        try:
            if guest_dir:
                self._cli(
                    "machine",
                    "exec",
                    "--name",
                    self._name,
                    "--",
                    "mkdir",
                    "-p",
                    guest_dir,
                    timeout=60,
                )
            with tempfile.NamedTemporaryFile("w", suffix=".seed", delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            try:
                self._cli(
                    "machine",
                    "cp",
                    tmp_path,
                    f"{self._name}:{guest_path}",
                    timeout=120,
                )
            finally:
                os.unlink(tmp_path)
        except Exception as err:
            logger.warning("smolvm: failed to seed %s: %s", guest_path, err)

    # -- SDK mode (registry images) ----------------------------------------
    def _sdk_start(self) -> None:
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

    def _sdk_run(self, cmd: list[str], prompt: str, prompt_guest: str, *, timeout: int) -> str:
        _, _, _, _, exec_options_cls = _smol()
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
        provider_files: dict[str, str] | None = None,
        smolvm_binary: str = "smolvm",
    ) -> None:
        self._agent_name = agent_name
        self._image = image
        self._name = name
        self._cwd = cwd
        self._timeout = timeout
        self._keep_on_error = keep_on_error
        self._keys = provider_keys_mapping or dict(_DEFAULT_PROVIDER_KEYS)
        self._provider_files = provider_files or {}
        self._smolvm_binary = smolvm_binary

    def available(self, agent: Agent) -> bool:
        # CLI modes (local file or local docker tag) need only the smolvm binary
        # (plus docker for the tag case); SDK/registry mode needs the smol extra.
        # Either is acceptable; a missing image ref is always unavailable.
        if not self._image:
            return False
        return shutil.which(self._smolvm_binary) is not None or SMOLVM_AVAILABLE

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
            auth_files=self._provider_files,
            smolvm_binary=self._smolvm_binary,
        )
