from __future__ import annotations

from superseded.agents.base import Agent
from superseded.agents.claude_code import ClaudeCodeAgent
from superseded.agents.codex import CodexAgent
from superseded.agents.opencode import OpenCodeAgent


def test_agent_is_abstract():
    import pytest

    with pytest.raises(TypeError):
        Agent()


def test_claude_code_agent_name():
    agent = ClaudeCodeAgent(model="claude-sonnet-4-6")
    assert agent.name == "claude-code"


def test_claude_code_build_command_with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    agent = ClaudeCodeAgent(model="claude-sonnet-4-6")
    cmd = agent.build_command()
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "-" in cmd
    assert "--bare" in cmd
    assert "--model" in cmd
    assert "claude-sonnet-4-6" in cmd


def test_claude_code_build_command_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    agent = ClaudeCodeAgent(model="claude-sonnet-4-6")
    cmd = agent.build_command()
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "-" in cmd
    assert "--bare" not in cmd
    assert "--model" in cmd
    assert "claude-sonnet-4-6" in cmd


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


def test_opencode_agent_name():
    agent = OpenCodeAgent()
    assert agent.name == "opencode"


def test_opencode_build_command():
    agent = OpenCodeAgent()
    cmd = agent.build_command()
    assert cmd[0] == "opencode"
    assert "run" in cmd


def test_codex_agent_name():
    agent = CodexAgent(model="gpt-5.4-mini")
    assert agent.name == "codex"


def test_codex_build_command():
    agent = CodexAgent(model="gpt-5.4-mini")
    cmd = agent.build_command()
    assert cmd[0] == "codex"
    assert "exec" in cmd
    assert "--json" in cmd
    assert "--model" in cmd
    assert "gpt-5.4-mini" in cmd


def test_codex_parses_jsonl_assistant_message():
    import json

    agent = CodexAgent()
    findings_json = json.dumps(
        [
            {
                "severity": "critical",
                "file": "x.py",
                "line": 1,
                "end_line": 2,
                "title": "t",
                "description": "d",
                "suggestion": "s",
            }
        ]
    )
    user_event = {"type": "message", "role": "user", "content": [{"type": "text", "text": "hi"}]}
    assistant_event = {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": f'Here:\n["noise"]... actually\n{findings_json}'}],
    }
    raw = json.dumps(user_event) + "\n" + json.dumps(assistant_event) + "\n"
    findings = agent.parse_output(raw, "correctness")
    assert len(findings) == 1
    assert findings[0]["severity"] == "critical"
    assert findings[0]["pass_name"] == "correctness"


def test_parse_ignores_bracketed_preamble():
    agent = ClaudeCodeAgent()
    raw = (
        "Here [note: see below] is the review:\n"
        '[{"severity": "critical", "file": "a.py", "line": 1, "end_line": 2, '
        '"title": "t", "description": "d", "suggestion": "s"}]'
    )
    findings = agent.parse_output(raw, "security")
    assert len(findings) == 1
    assert findings[0]["file"] == "a.py"


def test_parse_falls_back_to_markdown_when_no_json():
    agent = ClaudeCodeAgent()
    raw = (
        "## Findings\n\n"
        "### critical: SQL injection (security)\n"
        "src/auth.py:42 — user input in SQL\n\n"
        "Fix: use parameterized queries\n"
    )
    findings = agent.parse_output(raw, "security")
    assert len(findings) >= 1
    assert findings[0]["severity"] == "critical"
    assert findings[0]["pass_name"] == "security"
    assert "src/auth.py" in findings[0]["file"]


def test_extract_json_array_handles_large_input_without_catastrophic_backtracking():
    """Greedy .* under DOTALL must not cause catastrophic backtracking on big inputs."""
    import time

    from superseded.agents.parsing import extract_json_array

    large = "[{" + "x" * 50_000 + "}] not json"
    start = time.time()
    extract_json_array(large)
    elapsed = time.time() - start
    assert elapsed < 1.0, f"extract_json_array took {elapsed:.2f}s on 50KB input"


def test_extract_json_array_finds_nested_array():
    from superseded.agents.parsing import extract_json_array

    raw = 'preamble [{"severity": "critical", "file": "a.py", "line": 1}] trailing'
    result = extract_json_array(raw)
    assert result is not None
    assert len(result) == 1
    assert result[0]["severity"] == "critical"


def test_extract_json_array_finds_first_of_two_arrays():
    from superseded.agents.parsing import extract_json_array

    raw = (
        '[{"severity": "critical", "file": "a.py", "line": 1}] '
        "garbage between "
        '[{"severity": "nit", "file": "b.py", "line": 2}]'
    )
    result = extract_json_array(raw)
    assert result is not None
    assert len(result) == 1
    assert result[0]["severity"] == "critical"


def test_extract_json_array_recovers_when_closing_brace_appears_in_string():
    r"""A `}]` substring inside a string value must not truncate the array.

    Regression for the non-greedy regex `\[\s*\{.*?\}\s*\]` which returned the
    smallest match ending in `}]` and silently discarded the rest of the JSON.
    """
    from superseded.agents.parsing import extract_json_array

    raw = (
        "preamble\n"
        '[{"severity": "critical", "file": "a.py", "line": 1, '
        '"title": "beware }] injection", "suggestion": "s"}]\n'
        "trailing commentary\n"
    )
    result = extract_json_array(raw)
    assert result is not None
    assert len(result) == 1
    assert result[0]["title"] == "beware }] injection"
    assert result[0]["file"] == "a.py"


def test_extract_json_array_recovers_when_nested_braces_in_string():
    from superseded.agents.parsing import extract_json_array

    raw = (
        '[{"severity": "critical", "file": "a.py", "line": 1, '
        r'"description": "objects look like { \"a\": 1 }"}]'
    )
    result = extract_json_array(raw)
    assert result is not None
    assert len(result) == 1
    assert result[0]["severity"] == "critical"
