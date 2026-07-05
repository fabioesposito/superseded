from __future__ import annotations

import logging
import subprocess
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from superseded.review.executor import (
    SBX_AGENT_MAP,
    AgentRunError,
    SandboxExecutor,
    SubprocessExecutor,
    make_sandbox_executor,
)


def test_agent_run_error_is_runtime_error():
    assert issubclass(AgentRunError, RuntimeError)


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
        mock_run.side_effect = [
            _completed(),
            subprocess.TimeoutExpired(cmd=[], timeout=10),
            _completed(),
        ]
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
    executor = SandboxExecutor(agent_name="claude", name="sbx-1", cwd=tmp_path, keep_on_error=True)
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


def test_sandbox_session_exit_swallows_failing_rm(tmp_path, caplog):
    """Teardown (`sbx rm`) must never raise over the original result/error."""
    import logging

    executor = SandboxExecutor(agent_name="claude", name="sbx-1", cwd=tmp_path)
    with (
        patch("superseded.review.executor.subprocess.run") as mock_run,
        caplog.at_level(logging.WARNING, logger="superseded.review.executor"),
    ):
        mock_run.side_effect = [_completed(), subprocess.TimeoutExpired(cmd=[], timeout=60)]
        with executor.session():
            pass  # no exception escapes despite rm timing out
    assert any("sbx rm failed" in r.message for r in caplog.records)


def test_sandbox_session_keep_on_error_still_rms_on_success(tmp_path):
    """keep_on_error must NOT skip teardown when the run succeeded."""
    executor = SandboxExecutor(agent_name="claude", name="sbx-1", cwd=tmp_path, keep_on_error=True)
    with patch("superseded.review.executor.subprocess.run") as mock_run:
        mock_run.return_value = _completed(stdout="[]")
        with executor.session() as sess:
            assert sess.run(["claude"], "p", timeout=10) == "[]"
    cmds = [c.args[0] for c in mock_run.call_args_list]
    assert ["sbx", "rm", "sbx-1"] in cmds


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


def _install_fake_smol(
    monkeypatch, *, machine_create=None, write_file=None, exec_result=None, delete=None
):
    """Inject a fake `smol` package into sys.modules for one test.

    Returns a dict of the mock objects the test can assert against:
       {"Machine": ..., "MachineConfig": ..., "MountSpec": ...,
        "ResourceSpec": ..., "ExecOptions": ..., "machine_inst": ...}
    """
    if isinstance(exec_result, MagicMock):
        exec_result.return_value = exec_result
    machine_inst = types.SimpleNamespace(
        name="superseded-probe",
        write_file=MagicMock(side_effect=write_file or (lambda p, d, m=None: None)),
        exec=MagicMock(side_effect=exec_result or (lambda c, o=None: None)),
        delete=MagicMock(side_effect=delete or (lambda: None)),
        state=MagicMock(return_value="running"),
    )
    captured = {}

    class _Machine:
        @staticmethod
        def create(config=None, conn=None):
            captured["config"] = config
            if machine_create is not None:
                return machine_create(config)
            return machine_inst

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
    captured.update(
        {
            "Machine": _Machine,
            "MachineConfig": _MachineConfig,
            "MountSpec": _MountSpec,
            "ResourceSpec": _ResourceSpec,
            "ExecOptions": _ExecOptions,
            "machine_inst": machine_inst,
        }
    )
    return captured


def test_smolvm_executor_available_true_when_image_set_and_smol_importable(monkeypatch):
    _install_fake_smol(monkeypatch)
    from superseded.review import executor as exec_mod

    monkeypatch.setattr(exec_mod, "SMOLVM_AVAILABLE", True)
    from superseded.review.executor import SmolvmExecutor

    ex = SmolvmExecutor(agent_name="claude-code", image="ghcr.io/x/claude:1", name="superseded-x")
    assert ex.available(MagicMock()) is True


def test_smolvm_executor_available_false_when_image_empty(monkeypatch):
    _install_fake_smol(monkeypatch)
    from superseded.review.executor import SmolvmExecutor

    ex = SmolvmExecutor(agent_name="claude-code", image="", name="superseded-x")
    assert ex.available(MagicMock()) is False


def test_agent_credential_files_seeds_only_existing_host_files(tmp_path, monkeypatch):
    from superseded.review.executor import agent_credential_files

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    # opencode auth present, claude.json present (no secret, but file exists),
    # codex auth absent.
    oc = fake_home / ".local" / "share" / "opencode" / "auth.json"
    oc.parent.mkdir(parents=True)
    oc.write_text('{"opencode":{"key":"x"}}')
    (fake_home / ".claude.json").write_text('{"userID":"u"}')

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    opencode = agent_credential_files("opencode")
    assert "/root/.local/share/opencode/auth.json" in opencode
    assert opencode["/root/.local/share/opencode/auth.json"] == '{"opencode":{"key":"x"}}'

    claude = agent_credential_files("claude-code")
    assert "/root/.claude.json" in claude

    codex = agent_credential_files("codex")
    assert codex == {}  # no ~/.codex/auth.json on host

    # All seeded guest paths are /root-anchored so per-exec HOME relocation works.
    for agent in ("opencode", "claude-code", "codex"):
        for guest_path in agent_credential_files(agent):
            assert guest_path.startswith("/root/")


def test_make_sandbox_executor_smolvm_defaults_provider_files_from_agent(tmp_path, monkeypatch):
    # make_sandbox_executor should seed per-agent credential files by default
    # when provider_files is None (so the server path gets them for free).
    from superseded.review.executor import make_sandbox_executor

    fake_home = tmp_path / "home"
    (fake_home / ".local" / "share" / "opencode").mkdir(parents=True)
    (fake_home / ".local" / "share" / "opencode" / "auth.json").write_text("{}")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    ex = make_sandbox_executor(
        kind="smolvm",
        agent_name="opencode",
        name="x",
        cwd=tmp_path,
        resolved_image="img",
    )
    assert ex._provider_files == {"/root/.local/share/opencode/auth.json": "{}"}
    # explicit provider_files overrides the default
    ex2 = make_sandbox_executor(
        kind="smolvm",
        agent_name="opencode",
        name="y",
        cwd=tmp_path,
        resolved_image="img",
        provider_files={"/root/custom": "v"},
    )
    assert ex2._provider_files == {"/root/custom": "v"}


def test_smolvm_session_enter_creates_machine_with_workspace_mount(tmp_path, monkeypatch):
    captured = _install_fake_smol(monkeypatch)
    from superseded.review.executor import SmolvmExecutor

    ex = SmolvmExecutor(
        agent_name="claude-code", image="img", name="smol-1", cwd=tmp_path, timeout=30
    )
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
    _install_fake_smol(
        monkeypatch,
        machine_create=lambda cfg: (_ for _ in ()).throw(RuntimeError("kvm unavailable")),
    )
    from superseded.review.executor import SmolvmExecutor

    ex = SmolvmExecutor(agent_name="claude-code", image="img", name="smol-1", cwd=tmp_path)
    with pytest.raises(AgentRunError, match=r"smol Machine\.create failed"), ex.session():
        pass


def test_smolvm_session_exit_calls_delete(tmp_path, monkeypatch):
    captured = _install_fake_smol(monkeypatch)
    from superseded.review.executor import SmolvmExecutor

    ex = SmolvmExecutor(agent_name="claude-code", image="img", name="smol-1", cwd=tmp_path)
    with ex.session():
        pass
    captured["machine_inst"].delete.assert_called_once()


def test_smolvm_session_exit_keeps_machine_on_error_when_keep_on_error(tmp_path, monkeypatch):
    captured = _install_fake_smol(monkeypatch)
    from superseded.review.executor import SmolvmExecutor

    ex = SmolvmExecutor(
        agent_name="claude-code", image="img", name="smol-1", cwd=tmp_path, keep_on_error=True
    )
    sess = ex.session()
    sess._errored = True
    sess.__exit__(None, None, None)
    captured["machine_inst"].delete.assert_not_called()


def test_smolvm_session_exit_swallow_delete_failure(tmp_path, monkeypatch, caplog):
    captured = _install_fake_smol(
        monkeypatch, delete=lambda: (_ for _ in ()).throw(RuntimeError("nope"))
    )
    from superseded.review.executor import SmolvmExecutor

    ex = SmolvmExecutor(agent_name="claude-code", image="img", name="smol-1", cwd=tmp_path)
    with caplog.at_level(logging.WARNING, logger="superseded.review.executor"), ex.session():
        pass
    captured["machine_inst"].delete.assert_called_once()
    assert any("smol delete failed" in r.getMessage() for r in caplog.records)


def test_smolvm_session_run_uses_prompt_file_and_exec(tmp_path, monkeypatch):
    captured = _install_fake_smol(
        monkeypatch, exec_result=MagicMock(exit_code=0, stdout="[]", stderr="")
    )
    from superseded.review.executor import SmolvmExecutor

    ex = SmolvmExecutor(agent_name="claude-code", image="img", name="smol-1", cwd=tmp_path)
    with ex.session() as sess:
        out = sess.run(["claude", "-p"], "the-prompt", timeout=42)
    assert out == "[]"
    wf_call = captured["machine_inst"].write_file.call_args
    assert wf_call.args[0].startswith("/tmp/_smol_prompt_")
    assert wf_call.args[0].endswith(".txt")
    assert wf_call.args[1] == "the-prompt"
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
    captured = _install_fake_smol(
        monkeypatch, exec_result=MagicMock(exit_code=0, stdout="[]", stderr="")
    )
    monkeypatch.setattr("superseded.review.executor.os.environ", {"ANTHROPIC_API_KEY": "k-xyz"})
    from superseded.review.executor import SmolvmExecutor

    ex = SmolvmExecutor(agent_name="claude-code", image="img", name="smol-1", cwd=tmp_path)
    with ex.session() as sess:
        sess.run(["claude", "-p"], "p", timeout=10)
    opts = captured["machine_inst"].exec.call_args.args[1]
    assert opts.env == {"ANTHROPIC_API_KEY": "k-xyz"}


def test_smolvm_session_run_nonzero_raises(tmp_path, monkeypatch):
    _install_fake_smol(monkeypatch, exec_result=MagicMock(exit_code=2, stdout="", stderr="boom"))
    from superseded.review.executor import SmolvmExecutor

    ex = SmolvmExecutor(agent_name="claude-code", image="img", name="smol-1", cwd=tmp_path)
    with ex.session() as sess, pytest.raises(AgentRunError, match="boom"):
        sess.run(["claude"], "p", timeout=10)


def test_smolvm_session_run_exec_exception_raises_agent_run_error(tmp_path, monkeypatch):
    _install_fake_smol(
        monkeypatch,
        exec_result=lambda c, o=None: (_ for _ in ()).throw(RuntimeError("vm dead")),
    )
    from superseded.review.executor import SmolvmExecutor

    ex = SmolvmExecutor(agent_name="claude-code", image="img", name="smol-1", cwd=tmp_path)
    with ex.session() as sess, pytest.raises(AgentRunError, match="smol exec failed"):
        sess.run(["claude"], "p", timeout=10)


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

    ex = make_sandbox_executor(
        kind="smolvm",
        agent_name="claude-code",
        name="n1",
        cwd=tmp_path,
        resolved_image="ghcr.io/x/c:1",
    )
    assert isinstance(ex, SmolvmExecutor)
    assert ex._image == "ghcr.io/x/c:1"
    assert ex._name == "n1"
    assert ex._cwd == tmp_path


def test_make_sandbox_executor_kind_smolvm_without_image_raises(tmp_path):
    from superseded.review.executor import make_sandbox_executor

    with pytest.raises(ValueError, match="resolved_image"):
        make_sandbox_executor(
            kind="smolvm", agent_name="claude-code", name="n1", cwd=tmp_path, resolved_image=None
        )


def test_make_sandbox_executor_kind_smolvm_empty_image_raises(tmp_path):
    from superseded.review.executor import make_sandbox_executor

    with pytest.raises(ValueError, match="resolved_image"):
        make_sandbox_executor(
            kind="smolvm", agent_name="claude-code", name="n1", cwd=tmp_path, resolved_image=""
        )


def test_make_sandbox_executor_kind_unknown_raises(tmp_path):
    from superseded.review.executor import make_sandbox_executor

    with pytest.raises(ValueError, match="unknown sandbox kind"):
        make_sandbox_executor(kind="other", agent_name="claude-code", name="n1", cwd=tmp_path)
