from __future__ import annotations

from unittest.mock import patch

from superseded.diff import compute_file_context, parse_diff_files


def test_parse_diff_files():
    diff = """diff --git a/src/auth.py b/src/auth.py
index abc1234..def5678 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -10,6 +10,8 @@ def login():
     username = request.args.get("user")
 +    password = request.args.get("pass")
 +    return authenticate(username, password)
"""
    files = parse_diff_files(diff)
    assert len(files) == 1
    assert files[0]["file"] == "src/auth.py"
    assert "password" in files[0]["diff"]


def test_parse_diff_files_multiple():
    diff = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-old
+new
diff --git a/b.py b/b.py
--- a/b.py
+++ b/b.py
@@ -1 +1 @@
-foo
+bar
"""
    files = parse_diff_files(diff)
    assert len(files) == 2
    assert files[0]["file"] == "a.py"
    assert files[1]["file"] == "b.py"


def _file_tree():
    return {
        "src/auth.py": "\n".join(f"line {n}" for n in range(1, 60)),
    }


def test_compute_file_context_includes_surrounding_lines():
    diff = """diff --git a/src/auth.py b/src/auth.py
index abc..def 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -10,6 +10,8 @@ def login():
     username = request.args.get("user")
 +    password = request.args.get("pass")
 +    return authenticate(username, password)
"""
    with patch(
        "superseded.diff._read_file_lines", return_value=_file_tree()["src/auth.py"].splitlines()
    ):
        ctx = compute_file_context(diff, context_padding=20)
    assert "src/auth.py" in ctx
    assert "line 1" in ctx
    assert "line 30" in ctx


def test_compute_file_context_handles_missing_files():
    diff = """diff --git a/missing.py b/missing.py
--- a/missing.py
+++ b/missing.py
@@ -1 +1 @@
-old
+new
"""
    with patch("superseded.diff._read_file_lines", side_effect=FileNotFoundError):
        ctx = compute_file_context(diff)
    assert "missing.py" in ctx or ctx == ""
