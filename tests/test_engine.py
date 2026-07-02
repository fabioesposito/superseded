from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from superseded.models import Finding, ReviewResult
from superseded.review.engine import ReviewEngine


def make_finding(
    pass_name="security", severity="critical", file="a.py", line=1, title="test issue"
):
    return Finding(
        pass_name=pass_name,
        severity=severity,
        file=file,
        line=line,
        end_line=line + 1,
        title=title,
        description="desc",
        suggestion="fix",
    )


def test_engine_deduplicates():
    f1 = make_finding()
    f2 = make_finding()
    engine = ReviewEngine(agent=MagicMock(), config=MagicMock())
    result = engine.merge_findings([[f1], [f2]])
    assert len(result.findings) == 1


def test_engine_deduplicates_across_passes():
    """Two passes flagging the same file/line/title should collapse to one finding.

    `Finding.id` embeds pass_name, so the old `id`-based dedup never merged
    cross-pass duplicates. The merger must dedupe on file+line+title instead.
    """
    security = make_finding(pass_name="security", file="a.py", line=10, title="same bug")
    correctness = make_finding(pass_name="correctness", file="a.py", line=10, title="same bug")
    engine = ReviewEngine(agent=MagicMock(), config=MagicMock())
    result = engine.merge_findings([[security], [correctness]])
    assert len(result.findings) == 1
    # The surviving finding retains one of the contributing passes.
    assert result.findings[0].file == "a.py"
    assert result.findings[0].line == 10
    assert result.findings[0].title == "same bug"


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


def test_review_continues_when_one_pass_fails():
    engine = ReviewEngine(agent=MagicMock(), config=MagicMock())
    engine.config.is_pass_enabled = lambda name: True

    good_finding = make_finding(severity="critical", line=5)

    def fake_run_pass(pass_name, prompt, timeout=300, progress=None, sess=None):
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

    fake_session = MagicMock()
    fake_session.run.return_value = "x"
    with caplog.at_level(logging.WARNING, logger="superseded.review.engine"):
        findings = engine.run_pass("security", "prompt", sess=fake_session)
    assert len(findings) == 1
    assert findings[0].file == "a.py"
    assert "malformed" in caplog.text.lower() or "not-a-severity" in caplog.text
    fake_session.run.assert_called_once()


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
        "superseded.review.executor.subprocess.run",
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


def test_review_uses_injected_executor_session():
    """review() opens exactly one session on the injected executor and runs each pass through it."""
    engine = ReviewEngine(agent=MagicMock(), config=MagicMock(is_pass_enabled=lambda n: True))
    engine.agent.is_available.return_value = True
    engine.agent.build_command.return_value = ["echo"]
    engine.agent.parse_output.return_value = []

    fake_session = MagicMock()
    fake_session.run.return_value = "[]"
    fake_session.__enter__ = MagicMock(return_value=fake_session)
    fake_session.__exit__ = MagicMock(return_value=None)
    fake_executor = MagicMock()
    fake_executor.available.return_value = True
    fake_executor.session.return_value = fake_session

    engine.review(diff="d", passes=["security", "correctness"], executor=fake_executor)

    fake_executor.session.assert_called_once()
    assert fake_session.run.call_count == 2


def test_review_defaults_to_subprocess_executor(monkeypatch):
    """With no executor injected, a SubprocessExecutor is used and its availability checked."""
    monkeypatch.setattr(
        "superseded.review.executor.subprocess.run",
        lambda *a, **kw: MagicMock(returncode=0, stdout="[]", stderr=""),
    )
    agent = MagicMock()
    agent.is_available.return_value = True
    agent.build_command.return_value = ["echo"]
    agent.parse_output.return_value = []
    engine = ReviewEngine(agent=agent, config=MagicMock(is_pass_enabled=lambda n: True))
    engine.review(diff="d", passes=["security"])  # should not raise
