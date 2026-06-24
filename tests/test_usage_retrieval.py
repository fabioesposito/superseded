from __future__ import annotations

import keyword
from pathlib import Path
from unittest.mock import MagicMock, patch

from superseded.context.usage_retrieval import _KEYWORDS, extract_symbols, retrieve_usages


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


def test_extract_symbols_case_insensitive_dedupe_for_python():
    """Spec wants case-insensitive dedupe for Python/JS/TS."""
    diff = "+class MyClass: pass\n+def myclass(): pass"
    symbols = extract_symbols(diff, "python")
    assert len([s for s in symbols if s.lower() == "myclass"]) == 1


def test_retrieve_usages_single_rg_call():
    """Spec wants batched ripgrep, not one call per symbol."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "a.py:1:foo\n"

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        retrieve_usages("+def foo(): pass\n+def bar(): pass", Path("/tmp"))
        assert mock_run.call_count == 1
        cmd = mock_run.call_args[0][0]
        assert "foo" in cmd[-1] or "foo" in str(cmd)
        assert "bar" in cmd[-1] or "bar" in str(cmd)
