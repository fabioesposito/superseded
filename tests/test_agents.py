from superseded.agents.base import AgentAdapter, SubprocessAgentAdapter
from superseded.agents.claude_code import ClaudeCodeAdapter
from superseded.agents.opencode import OpenCodeAdapter
from superseded.models import AgentContext, AgentResult, Issue


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
    adapter = ClaudeCodeAdapter()
    cmd_parts = adapter._build_command("Write a plan for this feature.")
    assert "claude" in cmd_parts[0]
    assert "--print" in cmd_parts
    assert "Write a plan for this feature." in cmd_parts


def test_opencode_adapter_builds_command():
    adapter = OpenCodeAdapter()
    cmd_parts = adapter._build_command("Write a plan for this feature.")
    assert "opencode" in cmd_parts[0]
    assert "Write a plan for this feature." in cmd_parts


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


def test_claude_code_uses_worktree_when_set():
    ctx = AgentContext(
        repo_path="/tmp/repo",
        issue=Issue(
            id="SUP-001", title="Test", filepath=".superseded/issues/SUP-001-test.md"
        ),
        skill_prompt="Build this",
        artifacts_path=".superseded/artifacts/SUP-001",
        worktree_path="/tmp/repo/.superseded/worktrees/SUP-001",
    )
    adapter = ClaudeCodeAdapter()
    assert adapter._get_cwd(ctx) == "/tmp/repo/.superseded/worktrees/SUP-001"


def test_claude_code_uses_repo_when_no_worktree():
    ctx = AgentContext(
        repo_path="/tmp/repo",
        issue=Issue(
            id="SUP-001", title="Test", filepath=".superseded/issues/SUP-001-test.md"
        ),
        skill_prompt="Build this",
        artifacts_path=".superseded/artifacts/SUP-001",
    )
    adapter = ClaudeCodeAdapter()
    assert adapter._get_cwd(ctx) == "/tmp/repo"


def test_opencode_uses_worktree_when_set():
    ctx = AgentContext(
        repo_path="/tmp/repo",
        issue=Issue(
            id="SUP-001", title="Test", filepath=".superseded/issues/SUP-001-test.md"
        ),
        skill_prompt="Build this",
        artifacts_path=".superseded/artifacts/SUP-001",
        worktree_path="/tmp/repo/.superseded/worktrees/SUP-001",
    )
    adapter = OpenCodeAdapter()
    assert adapter._get_cwd(ctx) == "/tmp/repo/.superseded/worktrees/SUP-001"
