"""Live round-trip tests against the real Qwen (DashScope) API.

Skipped by default (addopts excludes `live`). To run:

    SUPERSEDED_QWEN_API_KEY=sk-... uv run pytest -m live tests/test_live_qwen.py -v
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _require_key():
    if not os.environ.get("SUPERSEDED_QWEN_API_KEY"):
        pytest.skip("SUPERSEDED_QWEN_API_KEY not set")


def test_live_qwen_complete_returns_content():
    from superseded.providers import QwenProvider

    provider = QwenProvider()
    resp = provider.complete("Reply with the single word: pong", timeout=30.0)
    assert resp.content
    assert resp.prompt_tokens > 0
    assert resp.completion_tokens > 0
