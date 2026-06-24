from __future__ import annotations

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
    big_match = "file.py:{}: call_to_sym()\n"
    matches = "".join(big_match.format(i) for i in range(200))

    def fake_run(cmd, **kwargs):
        if "rg" in cmd[0]:
            return MagicMock(returncode=0, stdout=matches, stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    result = retrieve_usages(
        "@@ -1,3 +1,5 @@\n+def sym():\n",
        Path("/repo"),
    )
    assert "omitted by retrieval budget" in result


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
