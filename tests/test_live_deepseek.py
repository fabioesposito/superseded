"""Live round-trip tests against the real DeepSeek API.

Skipped by default (addopts excludes `live`). To run:

    SUPERSEDED_DEEPSEEK_API_KEY=sk-... uv run pytest -m live tests/test_live_deepseek.py -v
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _require_key():
    if not os.environ.get("SUPERSEDED_DEEPSEEK_API_KEY"):
        pytest.skip("SUPERSEDED_DEEPSEEK_API_KEY not set")


def test_live_deepseek_complete_returns_content():
    from superseded.providers import DeepSeekProvider

    provider = DeepSeekProvider()
    resp = provider.complete("Reply with the single word: pong", timeout=30.0)
    assert resp.content
    assert resp.prompt_tokens > 0
    assert resp.completion_tokens > 0


def test_live_engine_review_on_small_diff():
    from superseded.config import Config
    from superseded.providers import DeepSeekProvider
    from superseded.review.engine import ReviewEngine

    diff = (
        "diff --git a/example.py b/example.py\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        "+++ b/example.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+import os\n"
        "+password = os.environ['PASSWORD']\n"
        "+print(password)\n"
    )
    provider = DeepSeekProvider()
    engine = ReviewEngine(provider=provider, config=Config(verify=False))
    result = engine.review(diff=diff, passes=["security"], timeout=60)
    # Don't over-assert on the model's output, but it should flag the secret exposure.
    assert isinstance(result.findings, list)
    assert result.usage.prompt_tokens > 0
