from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from superseded.audit.reflector import (
    PatternReflector,
    _build_reflection_prompt,
)
from superseded.providers import ProviderResponse


class FakeProvider:
    """Test double matching the Provider protocol."""

    name = "fake"

    def __init__(self, responses: list[str] | None = None, default: str = "[]"):
        self._responses = list(responses or [])
        self._default = default
        self.calls: list[str] = []

    def complete(
        self, prompt, *, model=None, timeout=600.0, temperature=0.0, reasoning_effort=None
    ):
        self.calls.append(prompt)
        if self._responses:
            return ProviderResponse(content=self._responses.pop(0))
        return ProviderResponse(content=self._default)


class FailingProvider(FakeProvider):
    """Provider whose complete() always raises (transport failure)."""

    def complete(
        self, prompt, *, model=None, timeout=600.0, temperature=0.0, reasoning_effort=None
    ):
        self.calls.append(prompt)
        raise RuntimeError("connection reset")


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture
def mock_store():
    store = MagicMock()
    store.get_reflection_state = AsyncMock(return_value=0)
    store.set_reflection_state = AsyncMock()
    store._db = MagicMock()
    return store


def _setup_mock_store_db(mock_store, rows):
    """Configure mock_store._db to yield a mock db with the given rows."""
    mock_cursor = AsyncMock()
    mock_cursor.fetchall = AsyncMock(return_value=rows)
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_cursor)

    def _make_ctx():
        @asynccontextmanager
        async def _fake_db():
            yield mock_db

        return _fake_db()

    mock_store._db = MagicMock(side_effect=_make_ctx)
    return mock_db, mock_cursor


def _make_feedback_rows(count: int, *, last_id: int = 0) -> list[tuple[Any, ...]]:
    """Generate mock feedback+findings rows."""
    rows = []
    for i in range(1, count + 1):
        fb_id = last_id + i
        action = "dismiss" if i % 2 == 0 else "helpful"
        rows.append(
            (
                fb_id,
                f"finding-{i}",
                action,
                "security",
                "high",
                f"file{i}.py",
                i * 10,
                f"Finding {i}",
                f"Description {i}",
            )
        )
    return rows


@pytest.mark.asyncio
async def test_below_threshold(provider, mock_store):
    """Fewer than REFLECTION_THRESHOLD feedback items returns []."""
    mock_store.get_reflection_state = AsyncMock(return_value=0)
    _setup_mock_store_db(mock_store, _make_feedback_rows(3))

    reflector = PatternReflector(provider, mock_store)
    result = await reflector.maybe_reflect("owner/repo")

    assert result == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_threshold_param_lowers_minimum_feedback(mock_store):
    """threshold=1 triggers reflection with fewer than the default 5 feedback items."""
    mock_store.get_reflection_state = AsyncMock(return_value=0)
    mock_store.set_reflection_state = AsyncMock()
    _setup_mock_store_db(mock_store, _make_feedback_rows(3))

    rules = [
        {"rule": "Do not flag naming conventions in test files", "evidence": "e", "confidence": 0.7}
    ]
    provider = FakeProvider(responses=[json.dumps(rules)])

    reflector = PatternReflector(provider, mock_store, threshold=1)
    result = await reflector.maybe_reflect("owner/repo")

    assert len(result) == 1
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_processes_feedback(mock_store):
    """Valid feedback triggers provider call and returns parsed rules."""
    mock_store.get_reflection_state = AsyncMock(return_value=0)
    mock_store.set_reflection_state = AsyncMock()
    _setup_mock_store_db(mock_store, _make_feedback_rows(6))

    rules = [
        {
            "rule": "Do not flag naming in test files",
            "evidence": "2 dismissals in test_*.py",
            "confidence": 0.85,
        }
    ]
    provider = FakeProvider(responses=[json.dumps(rules)])

    reflector = PatternReflector(provider, mock_store)
    result = await reflector.maybe_reflect("owner/repo")

    assert len(result) == 1
    assert result[0]["rule"] == "Do not flag naming in test files"
    assert result[0]["confidence"] == 0.85
    assert len(provider.calls) == 1
    mock_store.set_reflection_state.assert_called_once_with("owner/repo", 6)


@pytest.mark.asyncio
async def test_handles_provider_failure(mock_store):
    """Exception from provider.complete returns [] and updates state."""
    mock_store.get_reflection_state = AsyncMock(return_value=0)
    mock_store.set_reflection_state = AsyncMock()
    _setup_mock_store_db(mock_store, _make_feedback_rows(6))

    reflector = PatternReflector(FailingProvider(), mock_store)
    result = await reflector.maybe_reflect("owner/repo")

    assert result == []
    mock_store.set_reflection_state.assert_called_once_with("owner/repo", 6)


@pytest.mark.asyncio
async def test_handles_invalid_json(mock_store):
    """Provider returns garbage output → returns [], state still updated."""
    mock_store.get_reflection_state = AsyncMock(return_value=0)
    mock_store.set_reflection_state = AsyncMock()
    _setup_mock_store_db(mock_store, _make_feedback_rows(6))

    provider = FakeProvider(default="not json")
    reflector = PatternReflector(provider, mock_store)
    result = await reflector.maybe_reflect("owner/repo")

    assert result == []
    mock_store.set_reflection_state.assert_called_once_with("owner/repo", 6)


@pytest.mark.asyncio
async def test_empty_rules_updates_state(mock_store):
    """Provider returns [] — state IS updated (feedback was processed)."""
    mock_store.get_reflection_state = AsyncMock(return_value=0)
    mock_store.set_reflection_state = AsyncMock()
    _setup_mock_store_db(mock_store, _make_feedback_rows(6))

    reflector = PatternReflector(FakeProvider(), mock_store)
    result = await reflector.maybe_reflect("owner/repo")

    assert result == []
    mock_store.set_reflection_state.assert_called_once_with("owner/repo", 6)


@pytest.mark.asyncio
async def test_prompt_includes_both(mock_store):
    """Prompt contains ACCEPTED and DISMISSED sections."""
    mock_store.get_reflection_state = AsyncMock(return_value=0)
    mock_store.set_reflection_state = AsyncMock()

    rows = [
        (1, "f1", "helpful", "security", "high", "a.py", 10, "Title1", "Desc1"),
        (2, "f2", "dismiss", "correctness", "medium", "b.py", 20, "Title2", "Desc2"),
        (3, "f3", "dismiss", "performance", "low", "c.py", 30, "Title3", "Desc3"),
        (4, "f4", "helpful", "style", "info", "d.py", 40, "Title4", "Desc4"),
        (5, "f5", "dismiss", "security", "high", "e.py", 50, "Title5", "Desc5"),
        (6, "f6", "helpful", "architecture", "high", "f.py", 60, "Title6", "Desc6"),
    ]
    _setup_mock_store_db(mock_store, rows)

    provider = FakeProvider()
    reflector = PatternReflector(provider, mock_store)
    await reflector.maybe_reflect("owner/repo")

    assert len(provider.calls) == 1
    captured_prompt = provider.calls[0]
    assert "ACCEPTED findings" in captured_prompt
    assert "DISMISSED findings" in captured_prompt
    assert "<untrusted>" in captured_prompt
    assert "[security] Title1" in captured_prompt
    assert "[correctness] Title2" in captured_prompt
    assert "[performance] Title3" in captured_prompt


def test_build_reflection_prompt_empty():
    """Empty accepted/dismissed produces sections with no items."""
    prompt = _build_reflection_prompt([], [])
    assert "You are analyzing" in prompt
    assert "ACCEPTED findings:" not in prompt
    assert "DISMISSED findings:" not in prompt


def test_build_reflection_prompt_with_data():
    """Prompt includes formatted findings."""
    accepted = [{"pass": "security", "title": "SQL injection", "file": "db.py", "line": 42}]
    dismissed = [{"pass": "style", "title": "Naming issue", "file": "api.py", "line": 10}]
    prompt = _build_reflection_prompt(accepted, dismissed)
    assert "ACCEPTED findings" in prompt
    assert "[security] SQL injection (db.py:42)" in prompt
    assert "DISMISSED findings" in prompt
    assert "[style] Naming issue (api.py:10)" in prompt
    assert "<untrusted>" in prompt
    assert prompt.count("<untrusted>") == 2
    assert prompt.count("</untrusted>") == 2
