from __future__ import annotations

import yaml
from click.testing import CliRunner

from superseded.cli import cli
from superseded.config import load_config
from superseded.detection import AgentStatus


def _patch_detection(
    monkeypatch,
    *,
    agents: list[AgentStatus],
    gh: bool,
) -> None:
    monkeypatch.setattr("superseded.cli.detect_agents", lambda: agents)
    monkeypatch.setattr("superseded.cli.detect_gh", lambda: gh)


def test_init_happy_path(tmp_path, monkeypatch):
    _patch_detection(
        monkeypatch,
        agents=[
            AgentStatus("claude-code", True, "claude"),
            AgentStatus("opencode", True, "opencode"),
            AgentStatus("codex", False, "codex"),
        ],
        gh=True,
    )
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 0, result.output
    assert target.exists()
    cfg = load_config(target)
    assert cfg.agent == "claude-code"
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.passes.security is True
    assert cfg.passes.architecture is True


def test_init_refuses_overwrite_without_force(tmp_path, monkeypatch):
    target = tmp_path / ".superseded.yaml"
    target.write_text("agent: codex\n")
    _patch_detection(
        monkeypatch,
        agents=[AgentStatus("opencode", True, "opencode")],
        gh=True,
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 2
    assert yaml.safe_load(target.read_text()) == {"agent": "codex"}


def test_init_force_overwrites(tmp_path, monkeypatch):
    target = tmp_path / ".superseded.yaml"
    target.write_text("agent: codex\n")
    _patch_detection(
        monkeypatch,
        agents=[AgentStatus("claude-code", True, "claude")],
        gh=True,
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--force", "--config", str(target)])
    assert result.exit_code == 0
    cfg = load_config(target)
    assert cfg.agent == "claude-code"


def test_init_no_agents_exit_1(tmp_path, monkeypatch):
    _patch_detection(
        monkeypatch,
        agents=[
            AgentStatus("claude-code", False, "claude"),
            AgentStatus("opencode", False, "opencode"),
            AgentStatus("codex", False, "codex"),
        ],
        gh=True,
    )
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 1
    assert not target.exists()


def test_init_agent_override_unknown(tmp_path, monkeypatch):
    _patch_detection(
        monkeypatch,
        agents=[AgentStatus("opencode", True, "opencode")],
        gh=True,
    )
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--agent", "bogus", "--config", str(target)])
    assert result.exit_code == 2
    stderr = result.stderr_bytes.decode() if result.stderr_bytes else result.output
    assert "bogus" in stderr or "bogus" in result.output


def test_init_agent_override_not_installed(tmp_path, monkeypatch):
    _patch_detection(
        monkeypatch,
        agents=[
            AgentStatus("claude-code", True, "claude"),
            AgentStatus("codex", False, "codex"),
        ],
        gh=True,
    )
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--agent", "codex", "--config", str(target)])
    assert result.exit_code == 2
    assert not target.exists()


def test_init_agent_override_installed(tmp_path, monkeypatch):
    _patch_detection(
        monkeypatch,
        agents=[
            AgentStatus("claude-code", True, "claude"),
            AgentStatus("codex", True, "codex"),
        ],
        gh=True,
    )
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--agent", "codex", "--config", str(target)])
    assert result.exit_code == 0
    cfg = load_config(target)
    assert cfg.agent == "codex"
    assert cfg.model == "gpt-5.4-mini"


def test_init_gh_missing_still_succeeds(tmp_path, monkeypatch):
    _patch_detection(
        monkeypatch,
        agents=[AgentStatus("opencode", True, "opencode")],
        gh=False,
    )
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 0
    assert target.exists()
    cfg = load_config(target)
    assert cfg.agent == "opencode"
    assert cfg.model is None


def test_init_default_target_when_no_config_flag(tmp_path, monkeypatch):
    _patch_detection(
        monkeypatch,
        agents=[AgentStatus("opencode", True, "opencode")],
        gh=True,
    )
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 0
    assert (tmp_path / ".superseded.yaml").exists()
