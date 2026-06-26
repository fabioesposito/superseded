from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from superseded.diff import (
    _fetch_git_diff,
    _fetch_pr_diff,
    compute_file_context,
    parse_diff_files,
)


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


def test_parse_diff_files_handles_paths_with_spaces():
    diff = (
        'diff --git "a/my file.py" "b/my file.py"\n'
        "index abc..def 100644\n"
        '--- "a/my file.py"\n'
        '+++ "b/my file.py"\n'
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    files = parse_diff_files(diff)
    assert len(files) == 1
    assert files[0]["file"] == "my file.py"
    assert "my file.py" in files[0]["diff"]


def test_parse_diff_files_rename_uses_new_path():
    diff = """diff --git a/old.py b/new.py
similarity index 95%
rename from old.py
rename to new.py
--- a/old.py
+++ b/new.py
@@ -1 +1 @@
-old
+new
"""
    files = parse_diff_files(diff)
    assert len(files) == 1
    assert files[0]["file"] == "new.py"


def test_parse_diff_files_addition_uses_new_path():
    diff = """diff --git a/new.py b/new.py
new file mode 100644
--- /dev/null
+++ b/new.py
@@ -0,0 +1 @@
+created
"""
    files = parse_diff_files(diff)
    assert len(files) == 1
    assert files[0]["file"] == "new.py"


def test_parse_diff_files_deletion_falls_back_to_old_path():
    diff = """diff --git a/gone.py b/gone.py
deleted file mode 100644
--- a/gone.py
+++ /dev/null
@@ -1 +0 @@
-gone
"""
    files = parse_diff_files(diff)
    assert len(files) == 1
    assert files[0]["file"] == "gone.py"


def test_parse_diff_files_binary_without_minus_plus_lines():
    diff = """diff --git a/data.bin b/data.bin
index abc..def 100644
Binary files a/data.bin and b/data.bin differ
"""
    files = parse_diff_files(diff)
    assert len(files) == 1
    assert files[0]["file"] == "data.bin"


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


def test_compute_file_context_reads_relative_to_root(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "auth.py").write_text("\n".join(f"line {n}" for n in range(1, 60)))

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    diff = """diff --git a/src/auth.py b/src/auth.py
--- a/src/auth.py
+++ b/src/auth.py
@@ -10,6 +10,8 @@
 context
+new
"""
    ctx = compute_file_context(diff, root=tmp_path, context_padding=20)
    assert "src/auth.py" in ctx
    assert "line 1" in ctx
    assert "line 30" in ctx


def test_compute_file_context_rejects_path_traversal(tmp_path):
    """A diff with ../ in the file path must not read outside root."""
    repo = tmp_path / "repo"
    repo.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET")
    diff = """diff --git a/../secret.txt b/../secret.txt
--- a/../secret.txt
+++ b/../secret.txt
@@ -1 +1 @@
-old
+new
"""
    ctx = compute_file_context(diff, root=repo, context_padding=20)
    assert "TOP SECRET" not in ctx


def test_compute_file_context_rejects_sibling_with_shared_prefix(tmp_path):
    """A sibling directory sharing a name prefix must not be readable.

    Regression test for the str.startswith() prefix confusion: ``/parent/repo``
    vs ``/parent/repo-public`` would match under the old guard.
    """
    parent = tmp_path / "parent"
    parent.mkdir()
    repo = parent / "repo"
    repo.mkdir()
    sibling = parent / "repo-public"
    sibling.mkdir()
    secret = sibling / "secret.txt"
    secret.write_text("NEIGHBOR SECRET")
    (repo / "real.txt").write_text("ok line 1\n")

    diff = """diff --git a/../repo-public/secret.txt b/../repo-public/secret.txt
--- a/../repo-public/secret.txt
+++ b/../repo-public/secret.txt
@@ -1 +1 @@
-old
+new
"""
    ctx = compute_file_context(diff, root=repo, context_padding=20)
    assert "NEIGHBOR SECRET" not in ctx


def test_repo_root_returns_path(monkeypatch):
    from superseded.diff import repo_root

    mock = MagicMock(returncode=0, stdout="/mock/repo\n")
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock)
    result = repo_root()
    assert result == Path("/mock/repo")


def test_repo_root_falls_back_to_cwd(monkeypatch):
    from superseded.diff import repo_root

    def fail(*a, **kw):
        raise subprocess.CalledProcessError(1, "git")

    monkeypatch.setattr("subprocess.run", fail)
    result = repo_root()
    assert result == Path.cwd()


def test_fetch_pr_diff_raises_when_gh_not_found():
    def raise_fnf(*a, **kw):
        raise FileNotFoundError("gh")

    with (
        patch("subprocess.run", side_effect=raise_fnf),
        pytest.raises(RuntimeError, match=r"gh.*not found"),
    ):
        _fetch_pr_diff(1)


def test_fetch_pr_diff_surfaces_gh_failure():
    err = subprocess.CalledProcessError(
        returncode=4, cmd=["gh", "pr", "diff", "1"], output="", stderr="auth required"
    )

    with (
        patch("subprocess.run", side_effect=err),
        pytest.raises(RuntimeError, match=r"gh pr diff 1.*exit 4.*auth required"),
    ):
        _fetch_pr_diff(1)


def test_fetch_git_diff_raises_when_git_not_found():
    def raise_fnf(*a, **kw):
        raise FileNotFoundError("git")

    with (
        patch("subprocess.run", side_effect=raise_fnf),
        pytest.raises(RuntimeError, match=r"git.*not found"),
    ):
        _fetch_git_diff("HEAD~1..HEAD")


def test_fetch_pr_diff_forwards_timeout():
    from superseded.diff import _fetch_pr_diff

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return MagicMock(returncode=0, stdout="diff\n", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        _fetch_pr_diff(1)
    assert captured.get("timeout") is not None and captured["timeout"] > 0


def test_fetch_git_diff_forwards_timeout():
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return MagicMock(returncode=0, stdout="diff\n", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        _fetch_git_diff("HEAD~1..HEAD")
    assert captured.get("timeout") is not None and captured["timeout"] > 0


def test_repo_root_forward_timeout(monkeypatch):
    from superseded.diff import repo_root

    captured: dict = {}

    def fake_run(*a, **kw):
        captured.update(kw)
        return MagicMock(returncode=0, stdout="/mock/repo\n")

    monkeypatch.setattr("subprocess.run", fake_run)
    repo_root()
    assert captured.get("timeout") is not None and captured["timeout"] > 0
