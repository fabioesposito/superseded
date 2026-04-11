import tempfile
from pathlib import Path

from superseded.agents.base import SubprocessAgentAdapter
from superseded.db import Database
from superseded.models import Issue, Stage
from superseded.pipeline.context import ContextAssembler
from superseded.pipeline.events import PipelineEventManager
from superseded.pipeline.harness import HarnessRunner


class EchoAdapter(SubprocessAgentAdapter):
    def _build_command(self, prompt: str) -> list[str]:
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
            agent=agent,
            repo_path="/tmp/testrepo",
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
            db=db,
        )
        assert "Previous Session History" in context
        assert "build" in context.lower()

        # Verify current stage is NOT included in history
        context_same_stage = assembler.build(
            stage=Stage.BUILD,
            issue=issue,
            artifacts_path=str(artifacts_path),
            db=db,
        )
        assert "Previous Session History" not in context_same_stage

        await db.close()


async def test_streaming_with_retries_records_all_attempts():
    """Verify that retry attempts each get their own session turns."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(
            id="SUP-002",
            title="Retry test",
            filepath=".superseded/issues/SUP-002-test.md",
        )
        await db.upsert_issue(issue)

        class FailThenPassAdapter(SubprocessAgentAdapter):
            def __init__(self):
                super().__init__()
                self.call_count = 0

            def _build_command(self, prompt: str) -> list[str]:
                self.call_count += 1
                if self.call_count < 3:
                    return ["sh", "-c", "echo fail; exit 1"]
                return ["echo", "success"]

        agent = FailThenPassAdapter()
        event_manager = PipelineEventManager()
        runner = HarnessRunner(
            agent=agent,
            repo_path="/tmp/testrepo",
            max_retries=3,
            retryable_stages=["build"],
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

        assert result.passed is True
        assert agent.call_count == 3

        # 3 attempts = 3 user turns + 3 assistant turns = 6
        turns = await db.get_session_turns("SUP-002")
        assert len(turns) == 6

        await db.close()
