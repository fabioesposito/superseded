import json

import pytest
from pydantic import ValidationError

from superseded.models import AgentEvent, PipelineMetrics, SessionTurn, Stage


def test_session_turn_rejects_invalid_role():
    with pytest.raises(ValidationError):
        SessionTurn(role="bot", content="hello", stage=Stage.BUILD)


def test_session_turn_serializes_and_deserializes():
    turn = SessionTurn(
        role="user",
        content="Write a plan.",
        stage=Stage.PLAN,
        attempt=1,
        metadata={"key": "val"},
    )
    raw = turn.model_dump_json()
    restored = SessionTurn.model_validate_json(raw)
    assert restored.role == "user"
    assert restored.content == "Write a plan."
    assert restored.stage == Stage.PLAN
    assert restored.attempt == 1
    assert restored.metadata == {"key": "val"}


def test_agent_event_rejects_invalid_event_type():
    with pytest.raises(ValidationError):
        AgentEvent(event_type="debug", content="hello", stage=Stage.BUILD)


def test_agent_event_serializes_and_deserializes():
    event = AgentEvent(
        event_type="stdout",
        content="Building...",
        stage=Stage.BUILD,
        metadata={"exit_code": 0},
    )
    raw = event.model_dump_json()
    restored = AgentEvent.model_validate_json(raw)
    assert restored.event_type == "stdout"
    assert restored.content == "Building..."
    assert restored.stage == Stage.BUILD
    assert restored.metadata["exit_code"] == 0


def test_pipeline_metrics_serialization():
    metrics = PipelineMetrics(
        total_issues=5,
        issues_by_status={"new": 2, "done": 3},
        stage_success_rates={"build": 0.8},
        avg_stage_duration_ms={"build": 1200.0},
        total_retries=1,
        retries_by_stage={"build": 1},
    )
    data = metrics.model_dump()
    assert data["total_issues"] == 5
    assert data["issues_by_status"]["done"] == 3
    assert json.loads(metrics.model_dump_json())["stage_success_rates"]["build"] == 0.8
