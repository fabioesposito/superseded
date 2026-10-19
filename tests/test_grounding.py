from __future__ import annotations

from superseded.models import Finding
from superseded.review.grounding import (
    filter_static_duplicates,
    mark_pre_existing,
    validate_evidence,
)


def make_finding(**overrides):
    kwargs = dict(
        pass_name="security",
        severity="suggestion",
        file="a.py",
        line=3,
        title="issue",
        description="desc",
        suggestion="fix",
    )
    kwargs.update(overrides)
    return Finding(**kwargs)


DIFF = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,5 +1,5 @@
 context
-old
+new
 context
"""


def test_mark_pre_existing_line_inside_hunk():
    f = make_finding(line=3)
    mark_pre_existing([f], DIFF)
    assert f.pre_existing is False


def test_mark_pre_existing_line_outside_hunk():
    f = make_finding(line=99)
    mark_pre_existing([f], DIFF)
    assert f.pre_existing is True


def test_mark_pre_existing_file_absent_from_diff():
    f = make_finding(file="missing.py", line=3)
    mark_pre_existing([f], DIFF)
    assert f.pre_existing is True


def test_mark_pre_existing_empty_diff_leaves_false():
    f = make_finding(line=99)
    mark_pre_existing([f], "")
    assert f.pre_existing is False
    mark_pre_existing([f], "   ")
    assert f.pre_existing is False


def test_filter_static_duplicates_parses_ruff_mypy_gitleaks():
    static = (
        "src/a.py:10:1: E501 Line too long\n"
        "src/b.py:20: error: Incompatible return value\n"
        "  src/c.py:30 — gitleaks secret\n"
    )
    findings = [
        make_finding(severity="suggestion", file="src/a.py", line=10),
        make_finding(severity="suggestion", file="src/b.py", line=20),
        make_finding(severity="suggestion", file="src/c.py", line=30),
        make_finding(severity="suggestion", file="src/d.py", line=40),
    ]
    kept = filter_static_duplicates(findings, static)
    assert [f.file for f in kept] == ["src/d.py"]


def test_filter_static_duplicates_keeps_blocking_duplicates():
    static = "src/a.py:10:1: E501 Line too long\n"
    findings = [
        make_finding(severity="critical", file="src/a.py", line=10),
        make_finding(severity="important", file="src/a.py", line=10),
    ]
    kept = filter_static_duplicates(findings, static)
    assert len(kept) == 2
    assert all(f.duplicate_of_static for f in kept)


def test_filter_static_duplicates_no_signals_returns_all():
    findings = [make_finding(), make_finding(severity="nit", line=4)]
    assert filter_static_duplicates(findings, None) == findings
    assert filter_static_duplicates(findings, "") == findings


def test_validate_evidence_found_in_diff():
    f = make_finding(severity="suggestion", evidence="    return 2")
    kept = validate_evidence(
        [f],
        """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,3 +1,3 @@
 def foo():
-    return 1
+    return 2
""",
        None,
    )
    assert len(kept) == 1
    assert kept[0].evidence_verified is True


def test_validate_evidence_normalizes_whitespace_and_prefix():
    f = make_finding(severity="suggestion", evidence="+    return 2")
    diff = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,3 +1,3 @@
 def foo():
-    return 1
+    return 2
"""
    kept = validate_evidence([f], diff, None)
    assert len(kept) == 1
    assert kept[0].evidence_verified is True


def test_validate_evidence_found_in_file_context():
    file_context = "# a.py around line 3\n3:     return 2\n"
    f = make_finding(severity="suggestion", evidence="return 2")
    kept = validate_evidence([f], "", file_context)
    assert len(kept) == 1
    assert kept[0].evidence_verified is True


def test_validate_evidence_drops_unverified_suggestion():
    f = make_finding(severity="suggestion", evidence="totally absent line")
    kept = validate_evidence([f], DIFF, None)
    assert kept == []


def test_validate_evidence_keeps_unverified_critical_unverified():
    f = make_finding(severity="critical", evidence="totally absent line")
    kept = validate_evidence([f], DIFF, None)
    assert len(kept) == 1
    assert kept[0].evidence_verified is False


def test_validate_evidence_empty_evidence_is_unverified():
    f = make_finding(severity="suggestion", evidence="")
    kept = validate_evidence([f], DIFF, None)
    assert kept == []
