"""Live round-trip test against the real OpenAI API (GPT-5.6).

Skipped by default. To run:

    SUPERSEDED_OPENAI_API_KEY=sk-... uv run pytest -m live tests/test_live_openai.py -v
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _require_key():
    if not os.environ.get("SUPERSEDED_OPENAI_API_KEY"):
        pytest.skip("SUPERSEDED_OPENAI_API_KEY not set")


def test_live_openai_complete_returns_content():
    from superseded.providers import OpenAIProvider

    provider = OpenAIProvider()
    resp = provider.complete("Reply with the single word: pong", timeout=30.0)
    assert resp.content
    assert resp.prompt_tokens > 0
    assert resp.completion_tokens > 0
