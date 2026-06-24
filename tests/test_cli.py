from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from superseded.cli import cli, resolve_agent, resolve_model
from superseded.config import Config


def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_review_requires_pr_or_diff():
    runner = CliRunner()
    result = runner.invoke(cli, ["review"])
    assert result.exit_code != 0
    assert "pr" in result.output.lower() or "diff" in result.output.lower()


@patch("superseded.cli._run_review")
def test_review_with_pr(mock_review):
    mock_review.return_value = None
    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--pr", "123"])
    assert result.exit_code == 0
    mock_review.assert_called_once()


def test_resolve_agent_env_overrides_flag_and_config():
    with patch.dict("os.environ", {"SUPERSEDED_AGENT": "opencode"}, clear=False):
        assert resolve_agent(None, Config()) == "opencode"
        assert resolve_agent("codex", Config()) == "opencode"


def test_resolve_agent_flag_overrides_config_no_env():
    with patch.dict("os.environ", {}, clear=True):
        assert resolve_agent(None, Config()) == "claude-code"
        assert resolve_agent("codex", Config()) == "codex"


def test_resolve_model_env_overrides():
    with patch.dict("os.environ", {"SUPERSEDED_MODEL": "gpt-5"}, clear=False):
        assert resolve_model(None, Config()) == "gpt-5"
    with patch.dict("os.environ", {}, clear=True):
        assert resolve_model(None, Config(model="cfg-model")) == "cfg-model"
        assert resolve_model("flag-model", Config()) == "flag-model"
