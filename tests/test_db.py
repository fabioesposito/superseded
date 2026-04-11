import tempfile
from pathlib import Path

from superseded.db import Database
from superseded.models import HarnessIteration, Issue, IssueStatus, Stage, StageResult


async def test_db_initialize_and_operations():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / ".superseded" / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(
            id="SUP-001",
            title="Test issue",
            filepath=".superseded/issues/SUP-001-test.md",
        )
        await db.upsert_issue(issue)

        fetched = await db.get_issue("SUP-001")
        assert fetched is not None
        assert fetched["id"] == "SUP-001"
        assert fetched["title"] == "Test issue"
        assert fetched["status"] == "new"
        assert fetched["stage"] == "spec"

        all_issues = await db.list_issues()
        assert len(all_issues) == 1

        result = StageResult(
            stage=Stage.BUILD,
            passed=True,
            output="built successfully",
            artifacts=["src/main.py"],
        )
        await db.save_stage_result("SUP-001", result)

        results = await db.get_stage_results("SUP-001")
        assert len(results) == 1
        assert results[0]["stage"] == "build"
        assert results[0]["passed"] is True

        await db.close()


async def test_db_update_issue_status():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / ".superseded" / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(
            id="SUP-002",
            title="Another issue",
            filepath=".superseded/issues/SUP-002-another.md",
        )
        await db.upsert_issue(issue)

        await db.update_issue_status("SUP-002", IssueStatus.IN_PROGRESS, Stage.BUILD)
        fetched = await db.get_issue("SUP-002")
        assert fetched["status"] == "in-progress"
        assert fetched["stage"] == "build"

        await db.close()


async def test_db_harness_iterations():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / ".superseded" / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(
            id="SUP-100",
            title="Harness test",
            filepath=".superseded/issues/SUP-100-test.md",
        )
        await db.upsert_issue(issue)

        iteration = HarnessIteration(
            attempt=0,
            stage=Stage.BUILD,
            previous_errors=[],
        )
        await db.save_harness_iteration(
            "SUP-100", iteration, exit_code=0, output="ok", error=""
        )

        iterations = await db.get_harness_iterations("SUP-100")
        assert len(iterations) == 1
        assert iterations[0]["attempt"] == 0
        assert iterations[0]["stage"] == "build"
        assert iterations[0]["exit_code"] == 0

        await db.close()


async def test_save_stage_result_with_repo():
    """stage_results supports an optional repo column."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / ".superseded" / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(
            id="SUP-030",
            title="Multi-repo issue",
            filepath=".superseded/issues/SUP-030-test.md",
        )
        await db.upsert_issue(issue)

        result = StageResult(
            stage=Stage.BUILD,
            passed=True,
            output="ok",
        )
        await db.save_stage_result("SUP-030", result, repo="frontend")

        result2 = StageResult(
            stage=Stage.SPEC,
            passed=True,
            output="spec done",
        )
        await db.save_stage_result("SUP-030", result2)

        all_results = await db.get_stage_results("SUP-030")
        assert len(all_results) == 2

        frontend_results = await db.get_stage_results("SUP-030", repo="frontend")
        assert len(frontend_results) == 1
        assert frontend_results[0]["repo"] == "frontend"
        assert frontend_results[0]["stage"] == "build"

        primary_results = await db.get_stage_results("SUP-030", repo="primary")
        assert len(primary_results) == 1
        assert primary_results[0]["repo"] == "primary"
        assert primary_results[0]["stage"] == "spec"

        await db.close()


async def test_stage_result_repo_defaults_to_primary():
    """When no repo is specified, defaults to 'primary'."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / ".superseded" / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(
            id="SUP-031",
            title="Default repo test",
            filepath=".superseded/issues/SUP-031-test.md",
        )
        await db.upsert_issue(issue)

        result = StageResult(stage=Stage.BUILD, passed=True)
        await db.save_stage_result("SUP-031", result)

        results = await db.get_stage_results("SUP-031")
        assert results[0]["repo"] == "primary"

        await db.close()
