from __future__ import annotations

from superseded.detection import (
    AGENT_PREFERENCE,
    DEFAULT_MODELS,
    AgentStatus,
    default_model_for,
    detect_agents,
    detect_gh,
    pick_agent,
)


def test_agent_preference_order():
    assert AGENT_PREFERENCE == ("claude-code", "opencode", "codex")


def test_default_models_contains_known_agents():
    assert DEFAULT_MODELS["claude-code"] == "claude-sonnet-4-6"
    assert DEFAULT_MODELS["codex"] == "gpt-5.4-mini"
    assert DEFAULT_MODELS["opencode"] == "opencode/big-pickle"


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


def test_detect_agents_returns_all_three(monkeypatch):
    monkeypatch.setattr("superseded.detection.shutil.which", lambda b: f"/usr/bin/{b}")
    statuses = detect_agents()
    names = {s.name for s in statuses}
    assert names == {"claude-code", "opencode", "codex"}
    for s in statuses:
        assert s.available is True
        assert s.binary


def test_detect_agents_marks_missing_unavailable(monkeypatch):
    def fake_which(b: str) -> str | None:
        return None if b == "codex" else f"/usr/bin/{b}"

    monkeypatch.setattr("superseded.detection.shutil.which", fake_which)
    statuses = {s.name: s for s in detect_agents()}
    assert statuses["codex"].available is False
    assert statuses["opencode"].available is True


def test_detect_gh_true(monkeypatch):
    monkeypatch.setattr("superseded.detection.shutil.which", lambda b: "/usr/bin/gh")
    assert detect_gh() is True


def test_detect_gh_false(monkeypatch):
    monkeypatch.setattr("superseded.detection.shutil.which", lambda b: None)
    assert detect_gh() is False


def test_pick_agent_returns_highest_preference():
    assert pick_agent(["opencode", "codex"]) == "opencode"
    assert pick_agent(["claude-code", "codex"]) == "claude-code"
    assert pick_agent(["codex"]) == "codex"


def test_pick_agent_none_when_empty():
    assert pick_agent([]) is None


def test_default_model_for_known_agents():
    assert default_model_for("claude-code") == "claude-sonnet-4-6"
    assert default_model_for("codex") == "gpt-5.4-mini"


def test_default_model_for_opencode():
    assert default_model_for("opencode") == "opencode/big-pickle"


def test_default_model_for_unknown_is_none():
    assert default_model_for("bogus") is None
