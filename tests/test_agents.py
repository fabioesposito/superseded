from __future__ import annotations

from superseded.agents.base import Agent
from superseded.agents.claude_code import ClaudeCodeAgent


def test_agent_is_abstract():
    import pytest

    with pytest.raises(TypeError):
        Agent()


def test_claude_code_agent_name():
    agent = ClaudeCodeAgent(model="claude-sonnet-4-20250514")
    assert agent.name == "claude-code"


def test_claude_code_build_command():
    agent = ClaudeCodeAgent(model="claude-sonnet-4-20250514")
    cmd = agent.build_command("Review this code")
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "--bare" in cmd
    assert "--model" in cmd
    assert "claude-sonnet-4-20250514" in cmd


def test_claude_code_parse_output():
    agent = ClaudeCodeAgent()
    raw = """Here are the findings:
```json
[{"severity": "critical", "file": "a.py", "line": 1, "end_line": 2, "title": "t", "description": "d", "suggestion": "s"}]
```
"""
    findings = agent.parse_output(raw, "security")
    assert len(findings) == 1
    assert findings[0]["severity"] == "critical"
    assert findings[0]["pass_name"] == "security"
