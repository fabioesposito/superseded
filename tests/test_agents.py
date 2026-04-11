from superseded.agents.base import AgentAdapter, AgentContext, AgentResult
from superseded.agents.claude_code import ClaudeCodeAdapter
from superseded.agents.opencode import OpenCodeAdapter
from superseded.models import Issue


def _make_context(tmp: str) -> AgentContext:
    return AgentContext(
        repo_path=tmp,
        issue=Issue(
            id="SUP-001", title="Test", filepath=".superseded/issues/SUP-001-test.md"
        ),
        skill_prompt="Write a plan for this feature.",
        artifacts_path=".superseded/artifacts/SUP-001",
    )


def test_claude_code_adapter_builds_command():
    ctx = _make_context("/tmp/repo")
    adapter = ClaudeCodeAdapter()
    cmd_parts = adapter._build_command(ctx)
    assert "claude" in cmd_parts[0]
    assert "--print" in cmd_parts


def test_opencode_adapter_builds_command():
    ctx = _make_context("/tmp/repo")
    adapter = OpenCodeAdapter()
    cmd_parts = adapter._build_command(ctx)
    assert "opencode" in cmd_parts[0]


def test_adapter_protocol_enforced():
    assert hasattr(ClaudeCodeAdapter, "run")
    assert hasattr(OpenCodeAdapter, "run")
    assert isinstance(ClaudeCodeAdapter(), AgentAdapter)
    assert isinstance(OpenCodeAdapter(), AgentAdapter)


def test_adapter_timeout_default():
    adapter = ClaudeCodeAdapter()
    assert adapter.timeout == 600


def test_adapter_timeout_custom():
    adapter = ClaudeCodeAdapter(timeout=300)
    assert adapter.timeout == 300


def test_agent_result_model():
    result = AgentResult(exit_code=0, stdout="done", stderr="", files_changed=["a.py"])
    assert result.exit_code == 0
    assert result.files_changed == ["a.py"]
