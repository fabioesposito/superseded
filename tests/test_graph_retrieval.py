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
