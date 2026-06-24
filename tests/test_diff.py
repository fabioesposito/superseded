from __future__ import annotations

from superseded.diff import parse_diff_files


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
