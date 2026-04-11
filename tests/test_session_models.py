from superseded.models import AgentEvent, PipelineMetrics, SessionTurn, Stage


def test_session_turn_creation():
    turn = SessionTurn(
        role="user",
        content="Write a plan for this feature.",
        stage=Stage.SPEC,
        attempt=0,
    )
    assert turn.role == "user"
    assert turn.content == "Write a plan for this feature."
    assert turn.stage == Stage.SPEC
    assert turn.attempt == 0
    assert turn.metadata == {}


def test_session_turn_with_metadata():
    turn = SessionTurn(
        role="assistant",
        content="Plan written successfully.",
        stage=Stage.SPEC,
        attempt=0,
        metadata={"exit_code": 0, "files_changed": ["plan.md"]},
    )
    assert turn.metadata["exit_code"] == 0


def test_agent_event_stdout():
    event = AgentEvent(
        event_type="stdout",
        content="Building project...",
        stage=Stage.BUILD,
    )
    assert event.event_type == "stdout"
    assert event.content == "Building project..."
    assert event.stage == Stage.BUILD


def test_agent_event_status():
    event = AgentEvent(
        event_type="status",
        content="",
        stage=Stage.BUILD,
        metadata={"exit_code": 0, "duration_ms": 5432},
    )
    assert event.event_type == "status"
    assert event.metadata["exit_code"] == 0


def test_pipeline_metrics():
    metrics = PipelineMetrics(
        total_issues=10,
        issues_by_status={"done": 5, "in-progress": 3, "new": 2},
        stage_success_rates={"build": 0.8, "verify": 0.9},
        avg_stage_duration_ms={"build": 45000.0, "verify": 30000.0},
        total_retries=7,
        retries_by_stage={"build": 5, "verify": 2},
        recent_events=[],
    )
    assert metrics.total_issues == 10
    assert metrics.stage_success_rates["build"] == 0.8
