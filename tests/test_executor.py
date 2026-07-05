from __future__ import annotations

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
    from superseded.review.executor import SmolvmExecutor

    ex = SmolvmExecutor(agent_name="claude-code", image="ghcr.io/x/claude:1", name="superseded-x")
    assert ex.available(MagicMock()) is True


def test_smolvm_executor_available_false_when_image_empty(monkeypatch):
    _install_fake_smol(monkeypatch)
    from superseded.review.executor import SmolvmExecutor

    ex = SmolvmExecutor(agent_name="claude-code", image="", name="superseded-x")
    assert ex.available(MagicMock()) is False
