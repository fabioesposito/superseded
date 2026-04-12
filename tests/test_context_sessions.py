import tempfile
from pathlib import Path

from superseded.db import Database
from superseded.models import Issue, SessionTurn, Stage
from superseded.pipeline.context import ContextAssembler


async def test_session_history_layer_included():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(
            id="SUP-001",
            title="Test",
            filepath=".superseded/issues/SUP-001-test.md",
        )
        await db.upsert_issue(issue)

        # Create a dummy ticket file so the issue layer works
        ticket_path = Path(tmp) / ".superseded" / "issues" / "SUP-001-test.md"
        ticket_path.parent.mkdir(parents=True, exist_ok=True)
        ticket_path.write_text("---\nid: SUP-001\ntitle: Test\n---\n\nTest issue body")

        await db.save_session_turn(
            "SUP-001",
            SessionTurn(role="user", content="spec prompt", stage=Stage.SPEC, attempt=0),
        )
        await db.save_session_turn(
            "SUP-001",
            SessionTurn(
                role="assistant",
                content="spec output here",
                stage=Stage.SPEC,
                attempt=0,
            ),
        )

        assembler = ContextAssembler(tmp)
        result = assembler.build(
            stage=Stage.PLAN,
            issue=issue,
            artifacts_path="",
            db=db,
        )

        assert "Previous Session History" in result
        assert "spec prompt" in result
        assert "spec output here" in result

        await db.close()


async def test_session_history_excludes_current_stage():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(id="SUP-002", title="Test", filepath="")
        await db.upsert_issue(issue)

        await db.save_session_turn(
            "SUP-002",
            SessionTurn(role="user", content="build prompt", stage=Stage.BUILD, attempt=0),
        )

        assembler = ContextAssembler(tmp)
        result = assembler.build(
            stage=Stage.BUILD,
            issue=issue,
            artifacts_path="",
            db=db,
        )

        assert "Previous Session History" not in result

        await db.close()


async def test_session_history_truncates_long_output():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(id="SUP-003", title="Test", filepath="")
        await db.upsert_issue(issue)

        await db.save_session_turn(
            "SUP-003",
            SessionTurn(
                role="assistant",
                content="x" * 5000,
                stage=Stage.SPEC,
                attempt=0,
            ),
        )

        assembler = ContextAssembler(tmp)
        result = assembler.build(
            stage=Stage.PLAN,
            issue=issue,
            artifacts_path="",
            db=db,
        )

        # Should be truncated to 2000 chars
        assert "x" * 2001 not in result
        assert "truncated" in result.lower() or "x" * 2000 in result

        await db.close()


async def test_no_db_means_no_session_history():
    with tempfile.TemporaryDirectory() as tmp:
        issue = Issue(id="SUP-004", title="Test", filepath="")
        assembler = ContextAssembler(tmp)
        result = assembler.build(
            stage=Stage.PLAN,
            issue=issue,
            artifacts_path="",
            db=None,
        )
        assert "Previous Session History" not in result
