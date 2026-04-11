import tempfile
from pathlib import Path

from superseded.pipeline.plan import PlanTask, write_plan, read_plan


SAMPLE_PLAN = """# Plan: Add rate limiting

## Context
We need rate limiting on the API to prevent abuse.

## Tasks

### Task 1: Create rate limiter middleware
- **Description:** Add rate limiting middleware to the FastAPI app
- **Acceptance criteria:** Requests beyond limit receive 429 status code
- **Verification:** `uv run pytest tests/test_rate_limit.py -v`
- **Dependencies:** none
- **Scope:** Small

### Task 2: Add per-endpoint configuration
- **Description:** Allow configuring rate limits per endpoint via config
- **Acceptance criteria:** Different endpoints can have different rate limits
- **Verification:** `uv run pytest tests/test_rate_config.py -v`
- **Dependencies:** Task 1
- **Scope:** Medium
"""


def test_read_plan():
    with tempfile.TemporaryDirectory() as tmp:
        plan_path = Path(tmp) / "plan.md"
        plan_path.write_text(SAMPLE_PLAN)
        plan = read_plan(str(plan_path))
    assert plan.title == "Add rate limiting"
    assert len(plan.tasks) == 2
    assert plan.tasks[0].title == "Create rate limiter middleware"
    assert plan.tasks[0].scope == "Small"
    assert plan.tasks[1].dependencies == ["Task 1"]


def test_write_plan():
    with tempfile.TemporaryDirectory() as tmp:
        plan_path = Path(tmp) / "plan.md"
        plan = PlanTask(
            title="Add rate limiting",
            description="Add rate limiting middleware",
            acceptance_criteria=["429 on excess requests"],
            verification="pytest",
            dependencies=[],
            scope="Small",
        )
        write_plan(
            str(plan_path),
            title="Add rate limiting",
            context="We need rate limiting.",
            tasks=[plan],
        )
        content = plan_path.read_text()
    assert "# Plan: Add rate limiting" in content
    assert "Create rate limiter middleware" in content


def test_read_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        plan_path = Path(tmp) / "plan.md"
        task = PlanTask(
            title="Setup DB",
            description="Create database schema",
            acceptance_criteria=["Tables exist"],
            verification="pytest tests/test_db.py",
            dependencies=[],
            scope="Small",
        )
        write_plan(
            str(plan_path), title="Setup DB", context="Need a database", tasks=[task]
        )
        plan = read_plan(str(plan_path))
    assert plan.title == "Setup DB"
    assert len(plan.tasks) == 1
    assert plan.tasks[0].title == "Setup DB"
