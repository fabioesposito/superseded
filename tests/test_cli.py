from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from superseded.cli import cli, format_memory_context, resolve_agent, resolve_model
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


def test_format_memory_context_with_reasoning():
    dismissed = [
        {
            "pass": "performance",
            "title": "N+1 query",
            "reasoning": "Loops over 1000 rows; will hit DB N times per request.",
        }
    ]
    result = format_memory_context(dismissed)
    assert "N+1 query" in result
    assert "Loops over 1000 rows" in result
    assert "Rationale then was:" in result


def test_format_memory_context_without_reasoning():
    dismissed = [
        {
            "pass": "style",
            "title": "unclear naming",
            "reasoning": "",
        }
    ]
    result = format_memory_context(dismissed)
    assert "unclear naming" in result
    assert "Rationale then was:" not in result


def test_format_memory_context_truncates_long_reasoning():
    dismissed = [
        {
            "pass": "security",
            "title": "injection",
            "reasoning": "x" * 500,
        }
    ]
    result = format_memory_context(dismissed)
    assert len(result) < 600
    assert "\u2026" in result


def test_persist_findings_passes_reasoning(monkeypatch):
    from superseded.cli import _persist_findings
    from superseded.models import Finding, ReviewResult

    f = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        end_line=2,
        title="bad",
        description="d",
        suggestion="s",
        reasoning="suspicious input",
    )
    result = ReviewResult(findings=[f])

    calls = []

    async def async_record(**kwargs):
        calls.append(kwargs)

    mock_store = type("FakeStore", (), {})()
    mock_store.record_finding = staticmethod(async_record)

    _persist_findings(mock_store, result, "owner/repo")
    assert len(calls) == 1
    assert calls[0]["reasoning"] == "suspicious input"
