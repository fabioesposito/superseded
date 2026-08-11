"""Live round-trip test against the real Anthropic API (Claude).

Skipped by default. To run:

    SUPERSEDED_ANTHROPIC_API_KEY=sk-ant-... uv run pytest -m live tests/test_live_anthropic.py -v
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _require_key():
    if not os.environ.get("SUPERSEDED_ANTHROPIC_API_KEY"):
        pytest.skip("SUPERSEDED_ANTHROPIC_API_KEY not set")


def test_live_anthropic_complete_returns_content():
    from superseded.providers import AnthropicProvider

    provider = AnthropicProvider()
    resp = provider.complete("Reply with the single word: pong", timeout=30.0)
    assert resp.content
    assert resp.prompt_tokens > 0
    assert resp.completion_tokens > 0
