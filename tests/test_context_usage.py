from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

from superseded.context.usage_retrieval import (
    extract_symbols,
    retrieve_usages,
)


def test_extract_symbols_python():
    diff = """@@ -1,3 +1,5 @@
+def calculate_total(items):
+    class TaxCalculator:
+        pass
+TOTAL_RATE = 0.1
"""
    syms = extract_symbols(diff, "python")
    assert "calculate_total" in syms
    assert "TaxCalculator" in syms
    assert "TOTAL_RATE" in syms


def test_extract_symbols_go():
    diff = """@@ -1,3 +1,5 @@
+func HandleRequest(w http.ResponseWriter, r *http.Request) {
+type User struct {
+var DefaultTimeout = 30
+const MaxRetries = 3
"""
    syms = extract_symbols(diff, "go")
    assert "HandleRequest" in syms
    assert "User" in syms
    assert "DefaultTimeout" in syms
    assert "MaxRetries" in syms


def test_extract_symbols_js():
    diff = """@@ -1,3 +1,5 @@
+function fetchData() {
+class DataLoader {
+const MAX_RETRIES = 5;
+interface Config {
+type Options = {
"""
    syms = extract_symbols(diff, "js")
    assert "fetchData" in syms
    assert "DataLoader" in syms
    assert "MAX_RETRIES" in syms
    assert "Config" in syms
    assert "Options" in syms


def test_extract_symbols_dedup():
    diff = """@@ -1,3 +1,5 @@
+def foo():
+    foo()
"""
    syms = extract_symbols(diff, "python")
    assert syms.count("foo") == 1


def test_extract_symbols_cap():
    lines = "\n".join(f"+def func_{i}():" for i in range(50))
    diff = f"@@ -1,1 +1,50 @@\n{lines}"
    syms = extract_symbols(diff, "python")
    assert len(syms) <= 25


def test_extract_symbols_filters_keywords():
    diff = """@@ -1,3 +1,5 @@
+def process():
+    return None
"""
    syms = extract_symbols(diff, "python")
    assert "process" in syms
    assert "return" not in syms
    assert "None" not in syms


def test_extract_symbols_python_annotated_module_var():
    diff = "@@ -1,1 +1,2 @@\n+MAX_RETRIES: int = 5\n"
    syms = extract_symbols(diff, "python")
    assert "MAX_RETRIES" in syms


def test_extract_symbols_generic_for_unknown_lang():
    """lang=None (or any unrecognised value) must route to the generic regex,
    returning identifiers of 4+ chars while filtering keywords."""
    diff = "@@ -1,1 +1,3 @@\n+fn process_request() {\n+    self\n+}\n"
    syms = extract_symbols(diff, None)
    assert "process_request" in syms
    assert "self" not in syms  # filtered as a keyword


def test_generic_fallback_boosts_known_language_symbols():
    diff = "@@ -1 +1 @@\n+result = compute_things()\n"
    syms = extract_symbols(diff, "python")
    assert "compute_things" in syms


def test_rg_invocation(monkeypatch):
    def fake_run(cmd, **kwargs):
        if "rg" in cmd[0]:
            return MagicMock(returncode=0, stdout="other.py:10: foo()\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    result = retrieve_usages(
        "@@ -1,3 +1,5 @@\n+def foo():\n+    pass\n",
        Path("/repo"),
    )
    assert result is not None
    assert "foo()" in result


def test_rg_missing_returns_none(monkeypatch, caplog):
    def fail(*a, **kw):
        raise FileNotFoundError("no rg")

    monkeypatch.setattr("subprocess.run", fail)
    with caplog.at_level("WARNING"):
        result = retrieve_usages("@@ -1,3 +1,5 @@\n+def foo():\n", Path("/repo"))
    assert result is None
    assert "ripgrep not on PATH" in caplog.text


def test_budget_truncation(monkeypatch):
    big_match = "file.py:{}: sym()\n"
    matches = "".join(big_match.format(i) for i in range(400))

    def fake_run(cmd, **kwargs):
        if "rg" in cmd[0]:
            return MagicMock(returncode=0, stdout=matches, stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    result = retrieve_usages(
        "@@ -1,3 +1,5 @@\n+def sym():\n",
        Path("/repo"),
    )
    assert result is not None
    assert "omitted" in result


def test_no_symbols_returns_none():
    result = retrieve_usages("@@ -1,3 +1,5 @@\n unchanged\n", Path("/repo"))
    assert result is None


def test_changed_file_excluded_from_rg(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        if "rg" in cmd[0]:
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="other.py:5: call()\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    retrieve_usages(
        "diff --git a/foo.py b/foo.py\n@@ -1,3 +1,5 @@\n+def call():\n",
        Path("/repo"),
    )
    assert calls
    assert "--glob" in calls[0]


def test_multi_file_diff_extracts_symbols_from_all_files(monkeypatch):
    """A multi-file PR must extract symbols from every changed file (per its language)."""
    calls = []

    def fake_run(cmd, **kwargs):
        if "rg" in cmd[0]:
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="x:1: hit\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    diff = (
        "diff --git a/foo.py b/foo.py\n"
        "@@ -1,1 +1,2 @@\n"
        "+def spam():\n"
        "diff --git a/bar.go b/bar.go\n"
        "@@ -1,1 +1,2 @@\n"
        "+func Eggs():\n"
    )
    retrieve_usages(diff, Path("/repo"))

    assert calls, "ripgrep was never invoked"
    searched_patterns = [cmd[4] for cmd in calls]
    assert any("spam" in p for p in searched_patterns)
    assert any("Eggs" in p for p in searched_patterns)
    all_args = [arg for cmd in calls for arg in cmd]
    assert "!foo.py" in all_args
    assert "!bar.go" in all_args


def test_unknown_language_uses_generic_symbol_extraction(monkeypatch):
    """A diff with only unknown-language files (e.g. .rs) must still extract
    symbols via the generic regex and reach ripgrep, rather than being skipped."""
    calls = []

    def fake_run(cmd, **kwargs):
        if "rg" in cmd[0]:
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="lib.rs:10: process_request()\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    diff = (
        "diff --git a/src/lib.rs b/src/lib.rs\n"
        "@@ -1,1 +1,3 @@\n"
        "+fn process_request(input) {\n"
        "+    handle_response()\n"
        "+}\n"
    )
    result = retrieve_usages(diff, Path("/repo"))

    assert calls, "ripgrep was never invoked for unknown-language diff"
    searched_patterns = [cmd[4] for cmd in calls]
    assert any("process_request" in p for p in searched_patterns), (
        "generic symbol was not extracted from the .rs file"
    )
    assert result is not None
    assert "process_request" in result


def test_timeout_on_batched_rg_returns_none(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd="rg", timeout=15)

    monkeypatch.setattr("subprocess.run", fake_run)
    diff = "@@ -1 +1 @@\n+def fast():\n+def slow():\n"
    result = retrieve_usages(diff, Path("/repo"))
    assert result is None
