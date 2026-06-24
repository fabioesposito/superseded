from __future__ import annotations

import keyword

from superseded.context.usage_retrieval import _KEYWORDS, extract_symbols


def test_keywords_include_all_python_keywords():
    for kw in keyword.kwlist:
        assert kw in _KEYWORDS, f"Missing Python keyword: {kw}"


def test_extract_symbols_keeps_most_recent():
    """Spec wants most-recently-added first so focal change is retained."""
    lines = []
    for i in range(30):
        lines.append(f"+def func_{i}(): pass")
    diff = "\n".join(lines)
    symbols = extract_symbols(diff, "python")
    assert "func_29" in symbols
    assert "func_0" not in symbols
