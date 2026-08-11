from __future__ import annotations

from click.testing import CliRunner

from superseded.cli import cli


def test_init_writes_minimal_yaml(tmp_path, monkeypatch):
    target = tmp_path / ".superseded.yaml"
    monkeypatch.delenv("SUPERSEDED_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/gh" if cmd == "gh" else None)
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 0
    text = target.read_text()
    assert "provider: deepseek" in text


def test_init_refuses_overwrite_without_force(tmp_path):
    target = tmp_path / ".superseded.yaml"
    target.write_text("provider: deepseek\n")
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 2


def test_init_force_overwrites(tmp_path, monkeypatch):
    target = tmp_path / ".superseded.yaml"
    target.write_text("provider: deepseek\n")
    monkeypatch.delenv("SUPERSEDED_DEEPSEEK_API_KEY", raising=False)
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--force", "--config", str(target)])
    assert result.exit_code == 0


def test_init_reports_missing_deepseek_key(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERSEDED_DEEPSEEK_API_KEY", raising=False)
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 0  # not an error, just a status line
    assert "SUPERSEDED_DEEPSEEK_API_KEY: not set" in result.output


def test_init_reports_present_deepseek_key(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERSEDED_DEEPSEEK_API_KEY", "sk-test")
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 0
    assert "SUPERSEDED_DEEPSEEK_API_KEY: set" in result.output


def test_init_reports_gh_presence(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/gh" if cmd == "gh" else None)
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert "gh CLI: found" in result.output


def test_init_reports_gh_absence(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert "gh CLI: not found" in result.output


def test_init_default_target_when_no_config_flag(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 0
    assert (tmp_path / ".superseded.yaml").exists()


def test_init_crg_missing_prints_instruction(tmp_path, monkeypatch):
    """When no .code-review-graph dir exists, init prints the install-instruction
    line to stderr and still succeeds."""
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    monkeypatch.chdir(tmp_path)
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 0, result.output
    assert target.exists()
    assert "code-review-graph" in result.output
    assert "uv add code-review-graph" in result.output


def test_init_crg_present_prints_found(tmp_path, monkeypatch):
    """When a .code-review-graph dir exists, init reports it (package or not)."""
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    (tmp_path / ".code-review-graph").mkdir()
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 0, result.output
    assert "code-review-graph" in result.output
