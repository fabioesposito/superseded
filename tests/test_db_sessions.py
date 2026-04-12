import tempfile
from pathlib import Path

from superseded.db import Database
from superseded.models import AgentEvent, Issue, SessionTurn, Stage


async def test_save_and_get_session_turns():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / ".superseded" / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(id="SUP-001", title="Test", filepath="")
        await db.upsert_issue(issue)

        turn = SessionTurn(
            role="user",
            content="Write a spec for this feature.",
            stage=Stage.SPEC,
            attempt=0,
        )
        await db.save_session_turn("SUP-001", turn)

        turns = await db.get_session_turns("SUP-001")
        assert len(turns) == 1
        assert turns[0]["role"] == "user"
        assert turns[0]["content"] == "Write a spec for this feature."
        assert turns[0]["stage"] == "spec"

        await db.close()


async def test_get_session_turns_filters_by_stage():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / ".superseded" / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(id="SUP-002", title="Test", filepath="")
        await db.upsert_issue(issue)

        await db.save_session_turn(
            "SUP-002",
            SessionTurn(role="user", content="spec prompt", stage=Stage.SPEC, attempt=0),
        )
        await db.save_session_turn(
            "SUP-002",
            SessionTurn(role="assistant", content="spec output", stage=Stage.SPEC, attempt=0),
        )
        await db.save_session_turn(
            "SUP-002",
            SessionTurn(role="user", content="build prompt", stage=Stage.BUILD, attempt=0),
        )

        spec_turns = await db.get_session_turns("SUP-002", stage=Stage.SPEC)
        assert len(spec_turns) == 2

        all_turns = await db.get_session_turns("SUP-002")
        assert len(all_turns) == 3

        await db.close()


async def test_save_and_get_agent_events():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / ".superseded" / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(id="SUP-003", title="Test", filepath="")
        await db.upsert_issue(issue)

        event = AgentEvent(
            event_type="stdout",
            content="Building project...",
            stage=Stage.BUILD,
        )
        await db.save_agent_event("SUP-003", event)

        events = await db.get_agent_events("SUP-003")
        assert len(events) == 1
        assert events[0]["event_type"] == "stdout"
        assert events[0]["content"] == "Building project..."

        await db.close()


async def test_get_agent_events_limit():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / ".superseded" / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(id="SUP-004", title="Test", filepath="")
        await db.upsert_issue(issue)

        for i in range(5):
            await db.save_agent_event(
                "SUP-004",
                AgentEvent(event_type="stdout", content=f"line {i}", stage=Stage.BUILD),
            )

        events = await db.get_agent_events("SUP-004", limit=3)
        assert len(events) == 3
        # Events returned in chronological order (reversed from DESC query)
        assert events[0]["content"] == "line 2"

        await db.close()


async def test_get_recent_events_across_issues():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / ".superseded" / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        for issue_id in ["SUP-010", "SUP-011"]:
            await db.upsert_issue(Issue(id=issue_id, title="Test", filepath=""))
            await db.save_agent_event(
                issue_id,
                AgentEvent(event_type="stdout", content="output", stage=Stage.BUILD),
            )

        events = await db.get_recent_events(limit=10)
        assert len(events) == 2

        await db.close()
