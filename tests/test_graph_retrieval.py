from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock


def test_is_available_false_when_import_missing(tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "code_review_graph":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    from superseded.context.graph_retrieval import is_available

    assert is_available(tmp_path) is False


def test_is_available_false_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "code_review_graph", ModuleType("code_review_graph"))
    from superseded.context.graph_retrieval import is_available

    assert is_available(tmp_path) is False


def test_is_available_true(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "code_review_graph", ModuleType("code_review_graph"))
    (tmp_path / ".code-review-graph").mkdir()
    from superseded.context.graph_retrieval import is_available

    assert is_available(tmp_path) is True


def test_ensure_graph_fresh_swallows_file_not_found(monkeypatch, caplog):
    import subprocess

    def boom(*a, **kw):
        raise FileNotFoundError("no code-review-graph")

    monkeypatch.setattr(subprocess, "run", boom)
    from superseded.context.graph_retrieval import ensure_graph_fresh

    with caplog.at_level("WARNING"):
        ensure_graph_fresh(Path("/repo"))  # must not raise
    assert "code-review-graph" in caplog.text


def test_ensure_graph_fresh_swallows_timeout(monkeypatch, caplog):
    import subprocess

    def boom(*a, **kw):
        raise subprocess.TimeoutExpired(cmd=["code-review-graph"], timeout=30)

    monkeypatch.setattr(subprocess, "run", boom)
    from superseded.context.graph_retrieval import ensure_graph_fresh

    with caplog.at_level("WARNING"):
        ensure_graph_fresh(Path("/repo"))  # must not raise
    assert "timed out" in caplog.text


def test_ensure_graph_fresh_swallows_oserror(monkeypatch, caplog):
    import subprocess

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(subprocess, "run", boom)
    from superseded.context.graph_retrieval import ensure_graph_fresh

    with caplog.at_level("WARNING"):
        ensure_graph_fresh(Path("/repo"))  # must not raise


def test_ensure_graph_fresh_passes_cwd(monkeypatch):
    import subprocess

    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    from superseded.context.graph_retrieval import ensure_graph_fresh

    ensure_graph_fresh(Path("/repo"))
    assert seen["cmd"] == ["code-review-graph", "update", "--brief"]
    assert seen["kwargs"]["cwd"] == Path("/repo")
    assert seen["kwargs"]["timeout"] == 30


def _install_fake_query(monkeypatch, query_graph_impl):
    """Inject a fake code_review_graph.tools.query.query_graph callable."""
    fake_query_mod = ModuleType("code_review_graph.tools.query")
    fake_query_mod.query_graph = query_graph_impl
    fake_tools = ModuleType("code_review_graph.tools")
    fake_tools.query = fake_query_mod
    fake_module = ModuleType("code_review_graph")
    fake_module.tools = fake_tools
    monkeypatch.setitem(sys.modules, "code_review_graph", fake_module)
    monkeypatch.setitem(sys.modules, "code_review_graph.tools", fake_tools)
    monkeypatch.setitem(sys.modules, "code_review_graph.tools.query", fake_query_mod)


def test_query_callers_formats_path_line_name(monkeypatch):
    """Each caller becomes a 'path:line: caller_name' line (matches rg -n output)."""
    fake_result = {
        "status": "ok",
        "results": [
            {
                "qualified_name": "src/caller.py::do_thing",
                "name": "do_thing",
                "file_path": "src/caller.py",
                "line_start": 42,
            }
        ],
        "edges": [
            {
                "kind": "CALLS",
                "source": "src/caller.py::do_thing",
                "target": "src/lib.py::foobar",
                "file_path": "src/caller.py",
                "line": 45,
            }
        ],
    }
    captured = {}

    def fake_query_graph(pattern, target, repo_root):
        captured.update(pattern=pattern, target=target, repo_root=repo_root)
        return fake_result

    _install_fake_query(monkeypatch, fake_query_graph)

    from superseded.context.graph_retrieval import _query_callers

    lines = _query_callers("foobar", Path("/repo"))
    assert lines == ["src/caller.py:45: do_thing"]
    assert captured == {"pattern": "callers_of", "target": "foobar", "repo_root": "/repo"}


def test_query_callers_returns_empty_on_not_found(monkeypatch):
    _install_fake_query(
        monkeypatch,
        lambda pattern, target, repo_root: {
            "status": "not_found",
            "results": [],
            "edges": [],
        },
    )
    from superseded.context.graph_retrieval import _query_callers

    assert _query_callers("missing", Path("/repo")) == []


def test_query_callers_returns_empty_on_ambiguous(monkeypatch):
    _install_fake_query(
        monkeypatch,
        lambda pattern, target, repo_root: {
            "status": "ambiguous",
            "candidates": ["a.py::x", "b.py::x"],
            "results": [],
            "edges": [],
        },
    )
    from superseded.context.graph_retrieval import _query_callers

    assert _query_callers("x", Path("/repo")) == []


def test_query_callers_swallows_value_error(monkeypatch, caplog):
    """Bad repo_root raises ValueError inside CRG; we log and return []."""

    def boom(pattern, target, repo_root):
        raise ValueError("repo_root does not exist")

    _install_fake_query(monkeypatch, boom)
    from superseded.context.graph_retrieval import _query_callers

    with caplog.at_level("WARNING"):
        assert _query_callers("anything", Path("/repo")) == []
    assert "raised" in caplog.text


def test_query_callers_swallows_generic_exception(monkeypatch, caplog):
    """sqlite3 corruption or anything else should never abort the review."""

    def boom(pattern, target, repo_root):
        raise RuntimeError("db corrupted")

    _install_fake_query(monkeypatch, boom)
    from superseded.context.graph_retrieval import _query_callers

    with caplog.at_level("WARNING"):
        assert _query_callers("anything", Path("/repo")) == []
    assert "raised" in caplog.text


def test_query_callers_skips_non_calls_edges(monkeypatch):
    """Only CALLS edges count as caller relationships."""
    fake_result = {
        "status": "ok",
        "results": [
            {
                "qualified_name": "src/c.py::caller",
                "name": "caller",
                "file_path": "src/c.py",
                "line_start": 1,
            }
        ],
        "edges": [
            {
                "kind": "TESTED_BY",
                "source": "src/c.py::caller",
                "file_path": "src/c.py",
                "line": 2,
            },
            {
                "kind": "CALLS",
                "source": "src/c.py::caller",
                "file_path": "src/c.py",
                "line": 3,
            },
        ],
    }
    _install_fake_query(monkeypatch, lambda *a, **kw: fake_result)
    from superseded.context.graph_retrieval import _query_callers

    lines = _query_callers("sym", Path("/repo"))
    assert lines == ["src/c.py:3: caller"]


def test_retrieve_usages_via_graph_no_symbols_returns_none():
    from superseded.context.graph_retrieval import retrieve_usages_via_graph

    assert retrieve_usages_via_graph("@@ -1 +1 @@\n unchanged\n", Path("/repo")) is None


def test_retrieve_usages_via_graph_formats_blocks(monkeypatch):
    """For one symbol whose _query_callers returns lines, the output is a
    ### Usages of `symbol` block."""
    fake_result = {
        "status": "ok",
        "results": [
            {
                "qualified_name": "src/x.py::c0",
                "name": "c0",
                "file_path": "src/x.py",
                "line_start": 1,
            }
        ],
        "edges": [
            {"kind": "CALLS", "source": "src/x.py::c0", "file_path": "src/x.py", "line": 10},
            {"kind": "CALLS", "source": "src/x.py::c0", "file_path": "src/x.py", "line": 20},
        ],
    }
    _install_fake_query(monkeypatch, lambda *a, **kw: fake_result)
    from superseded.context.graph_retrieval import retrieve_usages_via_graph

    result = retrieve_usages_via_graph("@@ -1 +1 @@\n+def foobar():\n+    pass\n", Path("/repo"))
    assert result is not None
    assert "### Usages of `foobar`" in result
    assert "src/x.py:10" in result
    assert "src/x.py:20" in result


def test_retrieve_usages_via_graph_excludes_changed_files(monkeypatch):
    """Callers inside changed files should be filtered out — mirrors rg's
    --glob behavior."""
    fake_result = {
        "status": "ok",
        "results": [],
        "edges": [
            {
                "kind": "CALLS",
                "source": "src/changed.py::local",
                "file_path": "src/changed.py",
                "line": 5,
            },
            {
                "kind": "CALLS",
                "source": "src/other.py::ext",
                "file_path": "src/other.py",
                "line": 7,
            },
        ],
    }
    _install_fake_query(monkeypatch, lambda *a, **kw: fake_result)
    from superseded.context.graph_retrieval import retrieve_usages_via_graph

    diff = "diff --git a/src/changed.py b/src/changed.py\n@@ -1 +1,2 @@\n+def foo():\n+    pass\n"
    result = retrieve_usages_via_graph(diff, Path("/repo"))
    assert result is not None
    assert "src/other.py:7" in result
    assert "src/changed.py:5" not in result


def test_retrieve_usages_via_graph_budget_truncation(monkeypatch):
    """When the running block size exceeds USAGE_BUDGET, the tail-truncation
    marker is added."""
    # Each symbol returns many callers so total output exceeds USAGE_BUDGET (6000).
    edges = [
        {"kind": "CALLS", "source": f"src/x.py::c{i}", "file_path": "src/x.py", "line": ln}
        for i, ln in enumerate(range(1, 600))
    ]
    fake_result = {"status": "ok", "results": [], "edges": edges}
    _install_fake_query(monkeypatch, lambda *a, **kw: fake_result)
    from superseded.context.graph_retrieval import retrieve_usages_via_graph

    diff = "@@ -1 +1,2 @@\n+def foobar():\n+def baz():\n"
    result = retrieve_usages_via_graph(diff, Path("/repo"))
    assert result is not None
    assert "omitted" in result


def test_retrieve_usages_via_graph_returns_none_when_no_callers(monkeypatch):
    """All symbols yield empty caller lists -> None."""
    _install_fake_query(
        monkeypatch,
        lambda *a, **kw: {"status": "ok", "results": [], "edges": []},
    )
    from superseded.context.graph_retrieval import retrieve_usages_via_graph

    result = retrieve_usages_via_graph("@@ -1 +1 @@\n+def foo():\n+def bar():\n", Path("/repo"))
    assert result is None
