from superseded.agents.base import AgentAdapter
from superseded.agents.claude_code import ClaudeCodeAdapter
from superseded.agents.opencode import OpenCodeAdapter
from superseded.models import AgentContext, AgentResult, Issue


def _make_context(tmp: str) -> AgentContext:
    return AgentContext(
        repo_path=tmp,
        issue=Issue(id="SUP-001", title="Test", filepath=".superseded/issues/SUP-001-test.md"),
        skill_prompt="Write a plan for this feature.",
        artifacts_path=".superseded/artifacts/SUP-001",
    )


def test_claude_code_adapter_builds_command():
    adapter = ClaudeCodeAdapter()
    cmd_parts = adapter._build_command("Write a plan for this feature.")
    assert "claude" in cmd_parts[0]
    assert "--print" in cmd_parts
    # Prompt is now passed via stdin, not CLI args
    assert "Write a plan for this feature." not in cmd_parts


def test_opencode_adapter_builds_command():
    adapter = OpenCodeAdapter()
    cmd_parts = adapter._build_command("Write a plan for this feature.")
    assert "opencode" in cmd_parts[0]
    # Prompt is now passed via stdin, not CLI args
    assert "Write a plan for this feature." not in cmd_parts


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
        issue=Issue(id="SUP-001", title="Test", filepath=".superseded/issues/SUP-001-test.md"),
        skill_prompt="Build this",
        artifacts_path=".superseded/artifacts/SUP-001",
        worktree_path="/tmp/repo/.superseded/worktrees/SUP-001",
    )
    adapter = ClaudeCodeAdapter()
    assert adapter._get_cwd(ctx) == "/tmp/repo/.superseded/worktrees/SUP-001"


def test_claude_code_uses_repo_when_no_worktree():
    ctx = AgentContext(
        repo_path="/tmp/repo",
        issue=Issue(id="SUP-001", title="Test", filepath=".superseded/issues/SUP-001-test.md"),
        skill_prompt="Build this",
        artifacts_path=".superseded/artifacts/SUP-001",
    )
    adapter = ClaudeCodeAdapter()
    assert adapter._get_cwd(ctx) == "/tmp/repo"


def test_opencode_uses_worktree_when_set():
    ctx = AgentContext(
        repo_path="/tmp/repo",
        issue=Issue(id="SUP-001", title="Test", filepath=".superseded/issues/SUP-001-test.md"),
        skill_prompt="Build this",
        artifacts_path=".superseded/artifacts/SUP-001",
        worktree_path="/tmp/repo/.superseded/worktrees/SUP-001",
    )
    adapter = OpenCodeAdapter()
    assert adapter._get_cwd(ctx) == "/tmp/repo/.superseded/worktrees/SUP-001"


def test_prompt_not_in_argv():
    """Prompt should be passed via stdin, not as a CLI argument."""
    adapter = ClaudeCodeAdapter()
    cmd = adapter._build_command("malicious; rm -rf /")
    assert "malicious; rm -rf /" not in cmd
    assert adapter._get_stdin_data("malicious; rm -rf /") == b"malicious; rm -rf /"


def test_prompt_not_in_argv_opencode():
    """Prompt should be passed via stdin for OpenCode too."""
    adapter = OpenCodeAdapter()
    cmd = adapter._build_command("malicious; rm -rf /")
    assert "malicious; rm -rf /" not in cmd
    assert adapter._get_stdin_data("malicious; rm -rf /") == b"malicious; rm -rf /"


def test_claude_code_no_model():
    adapter = ClaudeCodeAdapter()
    cmd = adapter._build_command("test prompt")
    assert cmd == ["claude", "--print", "--output-format", "text"]


def test_claude_code_with_model():
    adapter = ClaudeCodeAdapter(model="claude-sonnet-4-20250514")
    cmd = adapter._build_command("test prompt")
    assert cmd == [
        "claude",
        "--print",
        "--output-format",
        "text",
        "--model",
        "claude-sonnet-4-20250514",
    ]


def test_claude_code_with_timeout():
    adapter = ClaudeCodeAdapter(timeout=300)
    assert adapter.timeout == 300


def test_claude_code_stdin():
    adapter = ClaudeCodeAdapter()
    data = adapter._get_stdin_data("hello")
    assert data == b"hello"


def test_opencode_no_model():
    adapter = OpenCodeAdapter()
    cmd = adapter._build_command("test prompt")
    assert cmd == ["opencode", "--non-interactive"]


def test_opencode_with_model():
    adapter = OpenCodeAdapter(model="gpt-4o")
    cmd = adapter._build_command("test prompt")
    assert cmd == ["opencode", "--non-interactive", "--model", "gpt-4o"]


def test_opencode_stdin():
    adapter = OpenCodeAdapter()
    data = adapter._get_stdin_data("hello")
    assert data == b"hello"


from superseded.agents.codex import CodexAdapter


def test_codex_no_model():
    adapter = CodexAdapter()
    cmd = adapter._build_command("test prompt")
    assert cmd == ["codex", "--quiet"]


def test_codex_with_model():
    adapter = CodexAdapter(model="o4-mini")
    cmd = adapter._build_command("test prompt")
    assert cmd == ["codex", "--quiet", "--model", "o4-mini"]


def test_codex_with_timeout():
    adapter = CodexAdapter(timeout=300)
    assert adapter.timeout == 300


def test_codex_stdin():
    adapter = CodexAdapter()
    data = adapter._get_stdin_data("hello")
    assert data == b"hello"
