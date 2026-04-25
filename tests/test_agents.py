from superseded.agents.base import AgentAdapter
from superseded.agents.claude_code import ClaudeCodeAdapter
from superseded.agents.codex import CodexAdapter
from superseded.agents.factory import AgentFactory
from superseded.agents.opencode import OpenCodeAdapter
from superseded.models import AgentContext, Issue


def _make_context(tmp: str) -> AgentContext:
    return AgentContext(
        repo_path=tmp,
        issue=Issue(id="SUP-001", title="Test", filepath=".superseded/issues/SUP-001-test.md"),
        skill_prompt="Write a plan for this feature.",
        artifacts_path=".superseded/artifacts/SUP-001",
    )


def test_claude_code_adapter_builds_command():
    adapter = ClaudeCodeAdapter()
    ctx = _make_context("/tmp")
    cmd_parts = adapter._build_command("Write a plan for this feature.", ctx)
    assert "claude" in cmd_parts[0]
    assert "-p" in cmd_parts
    assert "Write a plan for this feature." in cmd_parts


def test_opencode_adapter_builds_command():
    adapter = OpenCodeAdapter()
    ctx = _make_context("/tmp")
    cmd_parts = adapter._build_command("Write a plan for this feature.", ctx)
    assert "opencode" in cmd_parts[0]
    assert "run" in cmd_parts
    assert "Write a plan for this feature." in cmd_parts


def test_adapter_protocol_enforced():
    assert hasattr(ClaudeCodeAdapter, "run")
    assert hasattr(OpenCodeAdapter, "run")
    assert isinstance(ClaudeCodeAdapter(), AgentAdapter)
    assert isinstance(OpenCodeAdapter(), AgentAdapter)


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


def test_prompt_passed_as_arg():
    """Prompt is passed as a CLI argument to the agent command."""
    adapter = ClaudeCodeAdapter()
    ctx = _make_context("/tmp")
    cmd = adapter._build_command("Write a plan.", ctx)
    assert "Write a plan." in cmd
    assert adapter._get_stdin_data("Write a plan.") is None


def test_prompt_passed_as_arg_opencode():
    """Prompt is passed as a CLI argument to opencode run."""
    adapter = OpenCodeAdapter()
    ctx = _make_context("/tmp")
    cmd = adapter._build_command("Write a plan.", ctx)
    assert "Write a plan." in cmd
    assert adapter._get_stdin_data("Write a plan.") is None


def test_claude_code_no_model():
    adapter = ClaudeCodeAdapter()
    ctx = _make_context("/tmp")
    cmd = adapter._build_command("test prompt", ctx)
    assert cmd == [
        "claude",
        "-p",
        "test prompt",
        "--output-format",
        "text",
        "--model",
        "claude-sonnet-4-20250514",
    ]


def test_claude_code_with_model():
    adapter = ClaudeCodeAdapter(model="claude-sonnet-4-20250514")
    ctx = _make_context("/tmp")
    cmd = adapter._build_command("test prompt", ctx)
    assert cmd == [
        "claude",
        "-p",
        "test prompt",
        "--output-format",
        "text",
        "--model",
        "claude-sonnet-4-20250514",
    ]


def test_claude_code_stdin():
    adapter = ClaudeCodeAdapter()
    data = adapter._get_stdin_data("hello")
    assert data is None


def test_opencode_no_model():
    adapter = OpenCodeAdapter()
    ctx = _make_context("/tmp")
    cmd = adapter._build_command("test prompt", ctx)
    assert cmd == ["opencode", "run", "--pure", "test prompt"]


def test_opencode_with_model():
    adapter = OpenCodeAdapter(model="gpt-4o")
    ctx = _make_context("/tmp")
    cmd = adapter._build_command("test prompt", ctx)
    assert cmd == ["opencode", "-m", "gpt-4o", "run", "--pure", "test prompt"]


def test_opencode_stdin():
    adapter = OpenCodeAdapter()
    data = adapter._get_stdin_data("hello")
    assert data is None


def test_codex_no_model():
    adapter = CodexAdapter()
    ctx = _make_context("/tmp")
    cmd = adapter._build_command("test prompt", ctx)
    assert cmd == ["codex", "--quiet", "--model", "o4-mini", "test prompt"]


def test_codex_with_model():
    adapter = CodexAdapter(model="o4-mini")
    ctx = _make_context("/tmp")
    cmd = adapter._build_command("test prompt", ctx)
    assert cmd == ["codex", "--quiet", "--model", "o4-mini", "test prompt"]


def test_codex_stdin():
    adapter = CodexAdapter()
    data = adapter._get_stdin_data("hello")
    assert data is None


# --- AgentFactory tests ---


def test_factory_default():
    factory = AgentFactory()
    agent = factory.create()
    assert isinstance(agent, ClaudeCodeAdapter)
    assert agent.model == ""


def test_factory_claude_with_model():
    factory = AgentFactory()
    agent = factory.create(cli="claude-code", model="claude-sonnet-4-20250514")
    assert isinstance(agent, ClaudeCodeAdapter)
    assert agent.model == "claude-sonnet-4-20250514"


def test_factory_opencode():
    factory = AgentFactory()
    agent = factory.create(cli="opencode", model="gpt-4o")
    assert isinstance(agent, OpenCodeAdapter)
    assert agent.model == "gpt-4o"


def test_factory_codex():
    factory = AgentFactory()
    agent = factory.create(cli="codex", model="o4-mini")
    assert isinstance(agent, CodexAdapter)
    assert agent.model == "o4-mini"


def test_factory_custom_defaults():
    factory = AgentFactory(default_agent="opencode", default_model="gpt-4o", timeout=300)
    agent = factory.create()
    assert isinstance(agent, OpenCodeAdapter)
    assert agent.model == "gpt-4o"
    assert agent.timeout == 300


def test_factory_unknown_cli():
    import pytest

    factory = AgentFactory()
    with pytest.raises(ValueError, match="Unknown agent: bad"):
        factory.create(cli="bad")


def test_registry_contains_all_agents():
    from superseded.agents import get_registry

    registry = get_registry()
    assert "claude-code" in registry
    assert "opencode" in registry
    assert "codex" in registry


def test_registry_creates_correct_types():
    from superseded.agents import get_registry

    registry = get_registry()
    assert registry["claude-code"] is ClaudeCodeAdapter
    assert registry["opencode"] is OpenCodeAdapter
    assert registry["codex"] is CodexAdapter


def test_claude_code_injects_anthropic_key():
    adapter = ClaudeCodeAdapter(api_key="sk-ant-test")
    env = adapter._build_env()
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-test"
    assert "OPENAI_API_KEY" not in env


def test_opencode_injects_opencode_key():
    adapter = OpenCodeAdapter(api_key="oc-test")
    env = adapter._build_env()
    assert env["OPENCODE_API_KEY"] == "oc-test"
    assert "OPENAI_API_KEY" not in env


def test_codex_injects_openai_key():
    adapter = CodexAdapter(api_key="sk-openai-test")
    env = adapter._build_env()
    assert env["OPENAI_API_KEY"] == "sk-openai-test"
    assert "ANTHROPIC_API_KEY" not in env


def test_adapter_env_no_extra_keys_leak():
    adapter = CodexAdapter(api_key="sk-test", github_token="gh-test")
    env = adapter._build_env()
    assert env["GITHUB_TOKEN"] == "gh-test"
    assert env["OPENAI_API_KEY"] == "sk-test"
    assert "ANTHROPIC_API_KEY" not in env
    assert "OPENCODE_API_KEY" not in env


def test_factory_passes_api_keys():
    factory = AgentFactory(
        openai_api_key="sk-oai",
        anthropic_api_key="sk-ant",
        opencode_api_key="oc",
    )
    claude = factory.create(cli="claude-code")
    opencode = factory.create(cli="opencode")
    codex = factory.create(cli="codex")
    assert claude._api_key == "sk-ant"
    assert opencode._api_key == "oc"
    assert codex._api_key == "sk-oai"


def test_adapter_env_empty_when_no_keys():
    adapter = ClaudeCodeAdapter()
    env = adapter._build_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "GITHUB_TOKEN" not in env


def test_docker_adapter_claude():
    from superseded.agents.docker import DockerAgentAdapter

    adapter = DockerAgentAdapter(cli="claude-code", model="claude-sonnet-4-20250514")
    ctx = _make_context("/tmp")
    cmd = adapter._build_command("Write a plan.", ctx)
    assert cmd[0:3] == ["docker", "run", "--rm"]
    assert "-v" in cmd
    assert "/tmp:/workspace" in cmd
    assert "node:20-slim" in cmd
    assert "npx" in cmd
    assert "@anthropic-ai/claude-code" in cmd
    assert "--model" in cmd
    assert "claude-sonnet-4-20250514" in cmd


def test_docker_adapter_opencode():
    from superseded.agents.docker import DockerAgentAdapter

    adapter = DockerAgentAdapter(cli="opencode", model="gpt-4o")
    ctx = _make_context("/tmp/repo/.superseded/worktrees/SUP-001")
    cmd = adapter._build_command("Write a plan.", ctx)
    assert cmd[0:3] == ["docker", "run", "--rm"]
    assert "-v" in cmd
    assert "/tmp/repo/.superseded/worktrees/SUP-001:/workspace" in cmd
    assert "python:3.12-slim" in cmd
    assert "sh" in cmd
    assert "-c" in cmd

    # Check inner command and positional arguments
    sh_idx = cmd.index("sh")
    assert cmd[sh_idx + 1] == "-c"
    inner_cmd = cmd[sh_idx + 2]
    assert "pip install --user uv && ~/.local/bin/uvx opencode" in inner_cmd
    assert "-m gpt-4o" in inner_cmd
    assert 'run --pure "$1"' in inner_cmd

    assert cmd[sh_idx + 3] == "--"
    assert cmd[sh_idx + 4] == "Write a plan."


def test_factory_sandbox_docker():
    factory = AgentFactory()
    agent = factory.create(cli="opencode", sandbox="docker")
    from superseded.agents.docker import DockerAgentAdapter

    assert isinstance(agent, DockerAgentAdapter)
    assert agent.cli == "opencode"
