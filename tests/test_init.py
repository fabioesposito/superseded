from __future__ import annotations

import yaml
from click.testing import CliRunner

from superseded.cli import cli
from superseded.config import load_config


def test_init_happy_path(tmp_path):
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 0, result.output
    assert target.exists()
    cfg = load_config(target)
    assert cfg.provider == "deepseek"
    assert cfg.passes.security is True
    assert cfg.passes.architecture is True


def test_init_refuses_overwrite_without_force(tmp_path):
    target = tmp_path / ".superseded.yaml"
    target.write_text("provider: deepseek\n")
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 2
    assert yaml.safe_load(target.read_text()) == {"provider": "deepseek"}


def test_init_force_overwrites(tmp_path):
    target = tmp_path / ".superseded.yaml"
    target.write_text("provider: other\n")
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--force", "--config", str(target)])
    assert result.exit_code == 0
    cfg = load_config(target)
    assert cfg.provider == "deepseek"


def test_init_gh_missing_still_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 0
    assert target.exists()
    cfg = load_config(target)
    assert cfg.provider == "deepseek"


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


def test_init_deepseek_key_prompt(tmp_path, monkeypatch):
    """init reminds the user when SUPERSEDED_DEEPSEEK_API_KEY is unset."""
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    monkeypatch.delenv("SUPERSEDED_DEEPSEEK_API_KEY", raising=False)
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 0
    assert "SUPERSEDED_DEEPSEEK_API_KEY" in result.output
