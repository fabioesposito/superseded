from __future__ import annotations

import keyword

from superseded.context.usage_retrieval import _KEYWORDS


def test_keywords_include_all_python_keywords():
    for kw in keyword.kwlist:
        assert kw in _KEYWORDS, f"Missing Python keyword: {kw}"
