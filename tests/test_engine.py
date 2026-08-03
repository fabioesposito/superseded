from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from superseded.models import Finding, ReviewResult
from superseded.review.engine import ReviewEngine
from superseded.review.executor import SubprocessExecutor


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


def test_engine_dedup_keeps_highest_severity():
    """When two passes flag the same file/line/title at different severities,
    the higher-severity finding survives dedup (not merely the first seen)."""
    low = make_finding(severity="suggestion", file="a.py", line=10, title="same bug")
    high = make_finding(severity="critical", file="a.py", line=10, title="same bug")
    engine = ReviewEngine(agent=MagicMock(), config=MagicMock())
    result = engine.merge_findings([[low], [high]])
    assert len(result.findings) == 1
    assert result.findings[0].severity == "critical"


def test_engine_dedup_keeps_highest_severity_regardless_of_order():
    """Severity winner must not depend on which pass ran first."""
    high = make_finding(severity="critical", file="a.py", line=10, title="same bug")
    low = make_finding(severity="suggestion", file="a.py", line=10, title="same bug")
    engine = ReviewEngine(agent=MagicMock(), config=MagicMock())
    result = engine.merge_findings([[high], [low]])
    assert len(result.findings) == 1
    assert result.findings[0].severity == "critical"


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
    # Malformed findings trigger exactly one corrective retry (2 runs total),
    # never more. The retry carries the corrective nudge.
    assert fake_session.run.call_count == 2
    second_prompt = fake_session.run.call_args_list[1].args[1]
    assert "Correction" in second_prompt


def test_run_pass_retries_once_on_malformed_and_recovers():
    """A pass whose first response has malformed findings retries once with a
    corrective prompt; clean findings from the retry are used."""
    engine = ReviewEngine(agent=MagicMock(), config=MagicMock())
    valid = {
        "severity": "critical",
        "file": "a.py",
        "line": 1,
        "end_line": 2,
        "title": "ok",
        "description": "d",
        "suggestion": "s",
        "pass_name": "security",
    }
    bad = {
        "severity": "minor",
        "file": "b.py",
        "line": 1,
        "end_line": 1,
        "title": "bad",
        "description": "d",
        "suggestion": "s",
        "pass_name": "security",
    }
    recovered = {
        "severity": "important",
        "file": "b.py",
        "line": 1,
        "end_line": 1,
        "title": "fixed",
        "description": "d",
        "suggestion": "s",
        "pass_name": "security",
    }
    mock_agent = MagicMock()
    mock_agent.build_command.return_value = ["fake"]
    mock_agent.parse_output.side_effect = [[valid, bad], [recovered]]
    engine.agent = mock_agent

    fake_session = MagicMock()
    fake_session.run.return_value = "x"
    findings = engine.run_pass("security", "prompt", sess=fake_session)

    assert fake_session.run.call_count == 2
    # Retry output replaced the partial first attempt.
    assert len(findings) == 1
    assert findings[0].title == "fixed"
    # The corrective prompt was passed on the second run.
    second_prompt = fake_session.run.call_args_list[1].kwargs.get("input") or (
        fake_session.run.call_args_list[1].args[1]
        if len(fake_session.run.call_args_list[1].args) > 1
        else None
    )
    assert second_prompt is not None
    assert "Correction" in second_prompt or "correction" in second_prompt


def test_run_pass_retries_at_most_once():
    """Even if the retry also produces malformed findings, the pass does not loop."""
    engine = ReviewEngine(agent=MagicMock(), config=MagicMock())
    valid = {
        "severity": "critical",
        "file": "a.py",
        "line": 1,
        "end_line": 2,
        "title": "ok",
        "description": "d",
        "suggestion": "s",
        "pass_name": "security",
    }
    bad = {
        "severity": "minor",
        "file": "b.py",
        "line": 1,
        "end_line": 1,
        "title": "bad",
        "description": "d",
        "suggestion": "s",
        "pass_name": "security",
    }
    mock_agent = MagicMock()
    mock_agent.build_command.return_value = ["fake"]
    mock_agent.parse_output.return_value = [valid, bad]
    engine.agent = mock_agent

    fake_session = MagicMock()
    fake_session.run.return_value = "x"
    engine.run_pass("security", "prompt", sess=fake_session)
    assert fake_session.run.call_count == 2


def test_run_pass_does_not_retry_when_all_valid():
    """No malformed findings -> exactly one agent run, no retry."""
    engine = ReviewEngine(agent=MagicMock(), config=MagicMock())
    valid = {
        "severity": "critical",
        "file": "a.py",
        "line": 1,
        "end_line": 2,
        "title": "ok",
        "description": "d",
        "suggestion": "s",
        "pass_name": "security",
    }
    mock_agent = MagicMock()
    mock_agent.build_command.return_value = ["fake"]
    mock_agent.parse_output.return_value = [valid]
    engine.agent = mock_agent

    fake_session = MagicMock()
    fake_session.run.return_value = "x"
    engine.run_pass("security", "prompt", sess=fake_session)
    assert fake_session.run.call_count == 1


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


def test_review_fallback_executor_forwards_agent_name(monkeypatch):
    """The fallback SubprocessExecutor must receive agent_name for XDG isolation."""
    captured: dict = {}

    class CapturingExecutor(SubprocessExecutor):
        def __init__(self, agent_name=None):
            captured["agent_name"] = agent_name
            super().__init__(agent_name=agent_name)

    monkeypatch.setattr("superseded.review.engine.SubprocessExecutor", CapturingExecutor)
    monkeypatch.setattr(
        "superseded.review.executor.subprocess.run",
        lambda *a, **kw: MagicMock(returncode=0, stdout="[]", stderr=""),
    )
    agent = MagicMock()
    agent.name = "opencode"
    agent.is_available.return_value = True
    agent.build_command.return_value = ["echo"]
    agent.parse_output.return_value = []
    engine = ReviewEngine(agent=agent, config=MagicMock(is_pass_enabled=lambda n: True))
    engine.review(diff="d", passes=["security"])

    assert captured.get("agent_name") == "opencode"


def test_run_verification_keeps_all():
    """When verifier returns all 'keep', all findings are preserved."""
    from superseded.config import Config
    from superseded.models import Finding, ReviewResult

    engine = ReviewEngine(agent=MagicMock(), config=Config(verify=True))
    engine.agent.build_command.return_value = ["fake-agent"]
    engine.agent.parse_output.return_value = []
    engine.agent.name = "fake-agent"

    f1 = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        title="X",
        description="d",
        suggestion="s",
    )
    f2 = Finding(
        pass_name="style",
        severity="nit",
        file="b.py",
        line=2,
        title="Y",
        description="d",
        suggestion="s",
    )
    result = ReviewResult(findings=[f1, f2])

    mock_run = MagicMock(
        return_value='[{"id": "'
        + f1.id
        + '", "action": "keep", "reason": "ok"}, {"id": "'
        + f2.id
        + '", "action": "keep", "reason": "ok"}]'
    )

    new_result = engine._run_verification(result, "diff", "ctx", 600, MagicMock(run=mock_run))

    assert len(new_result.findings) == 2


def test_run_verification_drops_false_positives():
    """When verifier drops some findings, they are excluded."""
    from superseded.config import Config
    from superseded.models import Finding, ReviewResult

    engine = ReviewEngine(agent=MagicMock(), config=Config(verify=True))
    engine.agent.name = "fake-agent"

    f1 = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        title="Real",
        description="d",
        suggestion="s",
    )
    f2 = Finding(
        pass_name="style",
        severity="nit",
        file="b.py",
        line=2,
        title="Fake",
        description="d",
        suggestion="s",
    )
    result = ReviewResult(findings=[f1, f2])

    mock_run = MagicMock(
        return_value='[{"id": "'
        + f1.id
        + '", "action": "keep", "reason": "ok"}, {"id": "'
        + f2.id
        + '", "action": "drop", "reason": "false positive"}]'
    )

    new_result = engine._run_verification(result, "diff", "ctx", 600, MagicMock(run=mock_run))

    assert len(new_result.findings) == 1
    assert new_result.findings[0].id == f1.id
    assert f2.verification == "dropped"


def test_run_verification_reestimates_severity():
    """When verifier re-estimates severity, it is applied."""
    from superseded.config import Config
    from superseded.models import Finding, ReviewResult

    engine = ReviewEngine(agent=MagicMock(), config=Config(verify=True))
    engine.agent.name = "fake-agent"

    f = Finding(
        pass_name="performance",
        severity="important",
        file="a.py",
        line=5,
        title="Slow",
        description="d",
        suggestion="s",
    )
    result = ReviewResult(findings=[f])

    mock_run = MagicMock(
        return_value="[{"
        + '"id": "'
        + f.id
        + '", '
        + '"action": "keep", '
        + '"severity": "suggestion", '
        + '"confidence": "low", '
        + '"reason": "less severe than reported"'
        + "}]"
    )

    new_result = engine._run_verification(result, "diff", "ctx", 600, MagicMock(run=mock_run))

    assert len(new_result.findings) == 1
    assert new_result.findings[0].severity == "suggestion"
    assert new_result.findings[0].confidence == "low"
    assert new_result.findings[0].verified_severity == "suggestion"


def test_run_verification_failure_returns_original():
    """When verifier fails (non-zero exit), original findings are kept."""
    from superseded.config import Config
    from superseded.models import Finding, ReviewResult
    from superseded.review.executor import AgentRunError

    engine = ReviewEngine(agent=MagicMock(), config=Config(verify=True))
    engine.agent.name = "fake-agent"

    f = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        title="X",
        description="d",
        suggestion="s",
    )
    result = ReviewResult(findings=[f])

    mock_run = MagicMock(side_effect=AgentRunError("timeout"))

    new_result = engine._run_verification(result, "diff", "ctx", 600, MagicMock(run=mock_run))

    assert new_result is result
    assert len(new_result.warnings) == 1


def test_run_verification_missing_ids_kept():
    """Findings not in verifier output are kept unchanged."""
    from superseded.config import Config
    from superseded.models import Finding, ReviewResult

    engine = ReviewEngine(agent=MagicMock(), config=Config(verify=True))
    engine.agent.name = "fake-agent"

    f1 = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        title="Mentioned",
        description="d",
        suggestion="s",
    )
    f2 = Finding(
        pass_name="style",
        severity="nit",
        file="b.py",
        line=2,
        title="Omitted",
        description="d",
        suggestion="s",
    )
    result = ReviewResult(findings=[f1, f2])

    mock_run = MagicMock(return_value='[{"id": "' + f1.id + '", "action": "keep", "reason": "ok"}]')

    new_result = engine._run_verification(result, "diff", "ctx", 600, MagicMock(run=mock_run))

    assert len(new_result.findings) == 2


def test_run_verification_skips_when_no_findings():
    """Verification is skipped when there are no findings."""
    from superseded.config import Config
    from superseded.models import ReviewResult

    engine = ReviewEngine(agent=MagicMock(), config=Config(verify=True))
    engine.agent.name = "fake-agent"

    result = ReviewResult(findings=[])
    mock_run = MagicMock()

    new_result = engine._run_verification(result, "diff", "ctx", 600, MagicMock(run=mock_run))

    assert mock_run.call_count == 0
    assert new_result is result
