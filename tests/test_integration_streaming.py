import tempfile
from pathlib import Path

from superseded.agents.base import SubprocessAgentAdapter
from superseded.agents.factory import AgentFactory
from superseded.db import Database
from superseded.models import AgentContext, Issue, Stage
from superseded.pipeline.context import ContextAssembler
from superseded.pipeline.events import PipelineEventManager
from superseded.pipeline.harness import HarnessRunner


def _agent_factory(agent):
    factory = AgentFactory()
    factory.create = lambda **kwargs: agent
    return factory


class EchoAdapter(SubprocessAgentAdapter):
    def _build_command(self, prompt: str, context: AgentContext) -> list[str]:
        return ["echo", prompt]


async def test_full_streaming_pipeline():
    """End-to-end: agent streams → events saved → session turns saved → context injected."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(
            id="SUP-001",
            title="Integration test",
            filepath=".superseded/issues/SUP-001-test.md",
        )
        await db.upsert_issue(issue)

        agent = EchoAdapter()
        event_manager = PipelineEventManager()
        runner = HarnessRunner(
            agent_factory=_agent_factory(agent),
            repo_path=tmp,
            event_manager=event_manager,
        )

        artifacts_path = Path(tmp) / "artifacts"
        artifacts_path.mkdir()

        # Run BUILD stage with streaming
        result = await runner.run_stage_streaming(
            issue=issue,
            stage=Stage.BUILD,
            artifacts_path=str(artifacts_path),
            db=db,
            event_manager=event_manager,
        )

        # Verify stage passed
        assert result.passed is True
        assert result.output

        # Verify session turns were saved
        turns = await db.get_session_turns("SUP-001")
        assert len(turns) == 2
        assert turns[0]["role"] == "user"
        assert turns[1]["role"] == "assistant"
        assert turns[1]["stage"] == "build"

        # Verify agent events were saved
        events = await db.get_agent_events("SUP-001")
        assert len(events) >= 2  # at least stdout + status
        stdout_events = [e for e in events if e["event_type"] == "stdout"]
        assert len(stdout_events) >= 1

        # Verify session history is injected in next stage context
        assembler = ContextAssembler("/tmp/testrepo")
        context = assembler.build(
            stage=Stage.VERIFY,
            issue=issue,
            artifacts_path=str(artifacts_path),
            session_turns=turns,
        )
        assert "Previous Session History" in context
        assert "build" in context.lower()

        # Verify current stage is NOT included in history
        context_same_stage = assembler.build(
            stage=Stage.BUILD,
            issue=issue,
            artifacts_path=str(artifacts_path),
            session_turns=turns,
        )
        assert "Previous Session History" not in context_same_stage

        await db.close()


async def test_streaming_records_single_attempt():
    """Verify that a single run gets its own session turns."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(
            id="SUP-002",
            title="Single run test",
            filepath=".superseded/issues/SUP-002-test.md",
        )
        await db.upsert_issue(issue)

        class FailAdapter(SubprocessAgentAdapter):
            def _build_command(self, prompt: str, context: AgentContext) -> list[str]:
                return ["sh", "-c", "echo fail; exit 1"]

        agent = FailAdapter()
        event_manager = PipelineEventManager()
        runner = HarnessRunner(
            agent_factory=_agent_factory(agent),
            repo_path=tmp,
            event_manager=event_manager,
        )

        artifacts_path = Path(tmp) / "artifacts"
        artifacts_path.mkdir()

        result = await runner.run_stage_streaming(
            issue=issue,
            stage=Stage.BUILD,
            artifacts_path=str(artifacts_path),
            db=db,
            event_manager=event_manager,
        )

        assert result.passed is False
        assert "fail" in result.error

        # Single run = 1 user turn + 1 assistant turn = 2
        turns = await db.get_session_turns("SUP-002")
        assert len(turns) == 2

        await db.close()
