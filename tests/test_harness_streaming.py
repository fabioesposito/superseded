import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

from superseded.agents.factory import AgentFactory
from superseded.db import Database
from superseded.models import AgentEvent, AgentResult, Issue, Stage
from superseded.pipeline.events import PipelineEventManager
from superseded.pipeline.harness import HarnessRunner


def _mock_factory(mock_agent):
    factory = AgentFactory()
    factory.create = lambda **kwargs: mock_agent
    return factory


def _make_issue() -> Issue:
    return Issue(
        id="SUP-001",
        title="Test issue",
        filepath=".superseded/issues/SUP-001-test.md",
    )


async def test_streaming_saves_session_turns():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        mock_agent = AsyncMock()

        async def fake_stream(prompt, context):
            yield AgentEvent(event_type="stdout", content="building...", stage=Stage.BUILD)
            yield AgentEvent(
                event_type="status",
                content="",
                stage=Stage.BUILD,
                metadata={"exit_code": 0, "duration_ms": 1000},
            )

        mock_agent.run_streaming = fake_stream

        runner = HarnessRunner(agent_factory=_mock_factory(mock_agent), repo_path="/tmp/testrepo")
        event_manager = PipelineEventManager()

        artifacts_path = Path(tmp) / "artifacts"
        artifacts_path.mkdir()

        result = await runner.run_stage_streaming(
            issue=_make_issue(),
            stage=Stage.BUILD,
            artifacts_path=str(artifacts_path),
            db=db,
            event_manager=event_manager,
        )

        assert result.passed is True
        turns = await db.get_session_turns("SUP-001")
        assert len(turns) == 2
        assert turns[0]["role"] == "user"
        assert turns[1]["role"] == "assistant"

        await db.close()


async def test_streaming_saves_agent_events():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        mock_agent = AsyncMock()

        async def fake_stream(prompt, context):
            yield AgentEvent(event_type="stdout", content="line 1", stage=Stage.BUILD)
            yield AgentEvent(event_type="stdout", content="line 2", stage=Stage.BUILD)
            yield AgentEvent(
                event_type="status",
                content="",
                stage=Stage.BUILD,
                metadata={"exit_code": 0, "duration_ms": 500},
            )

        mock_agent.run_streaming = fake_stream

        runner = HarnessRunner(agent_factory=_mock_factory(mock_agent), repo_path="/tmp/testrepo")
        event_manager = PipelineEventManager()

        artifacts_path = Path(tmp) / "artifacts"
        artifacts_path.mkdir()

        await runner.run_stage_streaming(
            issue=_make_issue(),
            stage=Stage.BUILD,
            artifacts_path=str(artifacts_path),
            db=db,
            event_manager=event_manager,
        )

        events = await db.get_agent_events("SUP-001")
        assert len(events) == 3
        assert events[0]["content"] == "line 1"
        assert events[1]["content"] == "line 2"
        assert events[2]["event_type"] == "status"

        await db.close()


async def test_streaming_runs_once_on_failure():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        mock_agent = AsyncMock()
        mock_agent.run.return_value = AgentResult(exit_code=1, stdout="", stderr="error on build")

        runner = HarnessRunner(
            agent_factory=_mock_factory(mock_agent),
            repo_path="/tmp/testrepo",
        )

        artifacts_path = Path(tmp) / "artifacts"
        artifacts_path.mkdir()

        result = await runner.run_stage(
            issue=_make_issue(),
            stage=Stage.BUILD,
            artifacts_path=str(artifacts_path),
        )

        assert result.passed is False
        assert "error on build" in result.error
        assert mock_agent.run.call_count == 1

        await db.close()


async def test_streaming_truncates_long_output():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        mock_agent = AsyncMock()

        async def fake_stream(prompt, context):
            yield AgentEvent(
                event_type="stdout",
                content="x" * 5000,
                stage=Stage.BUILD,
            )
            yield AgentEvent(
                event_type="status",
                content="",
                stage=Stage.BUILD,
                metadata={"exit_code": 0, "duration_ms": 100},
            )

        mock_agent.run_streaming = fake_stream

        runner = HarnessRunner(agent_factory=_mock_factory(mock_agent), repo_path="/tmp/testrepo")
        event_manager = PipelineEventManager()

        artifacts_path = Path(tmp) / "artifacts"
        artifacts_path.mkdir()

        await runner.run_stage_streaming(
            issue=_make_issue(),
            stage=Stage.BUILD,
            artifacts_path=str(artifacts_path),
            db=db,
            event_manager=event_manager,
        )

        turns = await db.get_session_turns("SUP-001")
        assistant_turn = next(t for t in turns if t["role"] == "assistant")
        assert len(assistant_turn["content"]) <= 2000

        await db.close()
