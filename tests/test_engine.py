from __future__ import annotations

from unittest.mock import MagicMock

from superseded.models import Finding
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
