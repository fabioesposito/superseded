import tempfile
from pathlib import Path

from superseded.agents.base import SubprocessAgentAdapter
from superseded.models import AgentContext, Issue, Stage


class EchoAdapter(SubprocessAgentAdapter):
    def _build_command(self, prompt: str) -> list[str]:
        return ["echo", prompt]


class StderrAdapter(SubprocessAgentAdapter):
    def _build_command(self, prompt: str) -> list[str]:
        return ["sh", "-c", "echo oops >&2; exit 1"]


def _make_context() -> AgentContext:
    return AgentContext(
        repo_path="/tmp",
        issue=Issue(id="SUP-001", title="Test", filepath="", stage=Stage.BUILD),
        skill_prompt="test prompt",
    )


async def test_run_streaming_yields_stdout():
    adapter = EchoAdapter()
    events = []
    async for event in adapter.run_streaming("hello world", _make_context()):
        events.append(event)

    stdout_events = [e for e in events if e.event_type == "stdout"]
    status_events = [e for e in events if e.event_type == "status"]
    assert len(stdout_events) >= 1
    assert "hello world" in stdout_events[0].content
    assert len(status_events) == 1
    assert status_events[0].metadata["exit_code"] == 0


async def test_run_streaming_yields_stderr():
    adapter = StderrAdapter()
    events = []
    async for event in adapter.run_streaming("test", _make_context()):
        events.append(event)

    stderr_events = [e for e in events if e.event_type == "stderr"]
    status_events = [e for e in events if e.event_type == "status"]
    assert len(stderr_events) >= 1
    assert "oops" in stderr_events[0].content
    assert status_events[0].metadata["exit_code"] == 1


async def test_run_streaming_includes_duration():
    adapter = EchoAdapter()
    events = []
    async for event in adapter.run_streaming("test", _make_context()):
        events.append(event)

    status_events = [e for e in events if e.event_type == "status"]
    assert "duration_ms" in status_events[0].metadata
    assert status_events[0].metadata["duration_ms"] >= 0


async def test_run_streaming_timeout():

    class SlowAdapter(SubprocessAgentAdapter):
        def _build_command(self, prompt: str) -> list[str]:
            return ["sleep", "10"]

    adapter = SlowAdapter(timeout=1)
    events = []
    async for event in adapter.run_streaming("test", _make_context()):
        events.append(event)

    status_events = [e for e in events if e.event_type == "status"]
    assert status_events[0].metadata["exit_code"] == -1


async def test_run_non_streaming_success():
    adapter = EchoAdapter()
    result = await adapter.run("hello", _make_context())
    assert result.exit_code == 0
    assert "hello" in result.stdout
    assert result.stderr == ""


async def test_run_non_streaming_failure():
    adapter = StderrAdapter()
    result = await adapter.run("test", _make_context())
    assert result.exit_code == 1
    assert "oops" in result.stderr


async def test_run_non_streaming_timeout():
    class SlowAdapter(SubprocessAgentAdapter):
        def _build_command(self, prompt: str) -> list[str]:
            return ["sleep", "10"]

    adapter = SlowAdapter(timeout=1)
    result = await adapter.run("test", _make_context())
    assert result.exit_code == -1
    assert "timed out" in result.stderr


async def test_run_streaming_command_error():
    """Test streaming with a command that can't be found."""

    class BadCmdAdapter(SubprocessAgentAdapter):
        def _build_command(self, prompt: str) -> list[str]:
            return ["nonexistent_command_xyz"]

    adapter = BadCmdAdapter()
    events = []
    async for event in adapter.run_streaming("test", _make_context()):
        events.append(event)

    assert len(events) == 2
    assert events[0].event_type == "error"
    assert events[1].event_type == "status"
    assert events[1].metadata["exit_code"] == -1


async def test_run_uses_worktree_path():
    with tempfile.TemporaryDirectory() as tmp:
        worktree_path = Path(tmp) / "worktree"
        worktree_path.mkdir()

        context = AgentContext(
            repo_path=tmp,
            worktree_path=str(worktree_path),
            issue=Issue(id="SUP-001", title="Test", filepath="", stage=Stage.BUILD),
            skill_prompt="test",
        )

        class PwdAdapter(SubprocessAgentAdapter):
            def _build_command(self, prompt: str) -> list[str]:
                return ["pwd"]

        adapter = PwdAdapter()
        result = await adapter.run("test", context)
        assert result.exit_code == 0
        assert str(worktree_path) in result.stdout


async def test_run_uses_repo_path_when_no_worktree():
    context = AgentContext(
        repo_path="/tmp",
        issue=Issue(id="SUP-001", title="Test", filepath="", stage=Stage.BUILD),
        skill_prompt="test",
    )

    class PwdAdapter(SubprocessAgentAdapter):
        def _build_command(self, prompt: str) -> list[str]:
            return ["pwd"]

    adapter = PwdAdapter()
    result = await adapter.run("test", context)
    assert result.exit_code == 0
    assert "/tmp" in result.stdout
