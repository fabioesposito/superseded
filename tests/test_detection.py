from __future__ import annotations

from superseded.detection import (
    AGENT_PREFERENCE,
    DEFAULT_MODELS,
    AgentStatus,
)


def test_agent_preference_order():
    assert AGENT_PREFERENCE == ("claude-code", "opencode", "codex")


def test_default_models_contains_known_agents():
    assert DEFAULT_MODELS["claude-code"] == "claude-sonnet-4-6"
    assert DEFAULT_MODELS["codex"] == "gpt-5.4-mini"
    assert "opencode" not in DEFAULT_MODELS


def test_agent_status_is_frozen_dataclass():
    s = AgentStatus(name="opencode", available=True, binary="opencode")
    assert s.name == "opencode"
    assert s.available is True
    assert s.binary == "opencode"
    try:
        s.name = "x"  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("AgentStatus must be frozen")
