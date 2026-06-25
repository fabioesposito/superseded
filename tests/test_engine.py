from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from superseded.models import Finding, ReviewResult
from superseded.review.engine import ReviewEngine


def make_finding(pass_name="security", severity="critical", file="a.py", line=1):
    return Finding(
        pass_name=pass_name,
        severity=severity,
        file=file,
        line=line,
        end_line=line + 1,
        title="test issue",
        description="desc",
        suggestion="fix",
    )


def test_engine_deduplicates():
    f1 = make_finding()
    f2 = make_finding()
    engine = ReviewEngine(agent=MagicMock(), config=MagicMock())
    result = engine.merge_findings([[f1], [f2]])
    assert len(result.findings) == 1


def test_engine_sorts_by_severity():
    f1 = make_finding(severity="nit", line=1)
    f2 = make_finding(severity="critical", line=2)
    f3 = make_finding(severity="suggestion", line=3)
    engine = ReviewEngine(agent=MagicMock(), config=MagicMock())
    result = engine.merge_findings([[f1, f2, f3]])
    assert result.findings[0].severity == "critical"
    assert result.findings[-1].severity == "nit"


def test_engine_selects_agent():
    from superseded.agents.claude_code import ClaudeCodeAgent
    from superseded.agents.codex import CodexAgent
    from superseded.agents.opencode import OpenCodeAgent

    engine = ReviewEngine.select("claude-code", model="m")
    assert isinstance(engine.agent, ClaudeCodeAgent)

    engine = ReviewEngine.select("opencode", model="m")
    assert isinstance(engine.agent, OpenCodeAgent)

    engine = ReviewEngine.select("codex", model="m")
    assert isinstance(engine.agent, CodexAgent)


def _make_completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_pass_raises_on_nonzero_exit():
    agent = MagicMock()
    agent.build_command.return_value = ["fakeclaude"]
    agent.parse_output.return_value = []
    engine = ReviewEngine(agent=agent, config=MagicMock())
    with patch("superseded.review.engine.subprocess.run") as mock_run:
        mock_run.return_value = _make_completed(stderr="auth error", returncode=1)
        with pytest.raises(RuntimeError, match="auth error"):
            engine.run_pass("security", "prompt")


def test_run_pass_raises_on_timeout():
    agent = MagicMock()
    agent.build_command.return_value = ["fakeclaude"]
    agent.parse_output.return_value = []
    engine = ReviewEngine(agent=agent, config=MagicMock())
    with patch("superseded.review.engine.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["fakeclaude"], timeout=300)
        with pytest.raises(RuntimeError, match="timed out"):
            engine.run_pass("security", "prompt")


def test_review_continues_when_one_pass_fails():
    engine = ReviewEngine(agent=MagicMock(), config=MagicMock())
    engine.config.is_pass_enabled = lambda name: True

    good_finding = make_finding(severity="critical", line=5)

    def fake_run_pass(pass_name, prompt, timeout=300, progress=None):
        if pass_name == "correctness":
            raise RuntimeError("boom")
        return [good_finding]

    engine.run_pass = fake_run_pass  # type: ignore[method-assign]
    result = engine.review(diff="diff", passes=["security", "correctness"])
    assert isinstance(result, ReviewResult)
    assert len(result.findings) == 1
    assert result.findings[0] is good_finding


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
    with patch("superseded.review.engine.subprocess.run") as mock_run:
        mock_run.return_value = _make_completed(stdout="x")
        with caplog.at_level(logging.WARNING, logger="superseded.review.engine"):
            findings = engine.run_pass("security", "prompt")
    assert len(findings) == 1
    assert findings[0].file == "a.py"
    assert "malformed" in caplog.text.lower() or "not-a-severity" in caplog.text


def test_review_raises_when_agent_unavailable(monkeypatch):
    engine = ReviewEngine.select("claude-code", model=None)
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    with pytest.raises(RuntimeError, match=r"agent|PATH|not found"):
        engine.review(diff="diff --git a/x.py b/x.py\n")


def test_review_forwards_conventions_and_spec_signals(monkeypatch):
    captured: dict[str, str | None] = {}

    def fake_build_prompt(**kw):
        captured.update(kw)
        return "prompt"

    monkeypatch.setattr("superseded.review.engine.build_prompt", fake_build_prompt)
    monkeypatch.setattr(
        "superseded.review.engine.subprocess.run",
        lambda *a, **kw: MagicMock(returncode=0, stdout="[]", stderr=""),
    )

    agent = MagicMock()
    agent.is_available.return_value = True
    agent.build_command.return_value = ["echo"]
    agent.parse_output.return_value = []
    engine = ReviewEngine(agent=agent, config=MagicMock(is_pass_enabled=lambda n: True))

    engine.review(
        diff="diff",
        conventions_signals="conv-block",
        spec_signals="spec-block",
        passes=["security"],
    )
    assert captured.get("conventions_signals") == "conv-block"
    assert captured.get("spec_signals") == "spec-block"
