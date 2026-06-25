from __future__ import annotations

from superseded.context.conventions import BLOCKLIST, strip_blocklisted_sections


def test_strip_removes_blocklisted_section_and_body():
    doc = (
        "# AGENTS.md\n\n"
        "## Conventions\n\nKeep this.\n\n"
        "## Toolchain & environment\n\nDrop this body.\n\n"
        "## Architecture notes\n\nKeep this too.\n"
    )
    out = strip_blocklisted_sections(doc)
    assert "Keep this." in out
    assert "Keep this too." in out
    assert "Toolchain" not in out
    assert "Drop this body." not in out


def test_strip_is_case_insensitive_substring_match():
    doc = "## PACKAGING / GitHub Action\n\nbody to drop\n\n## Conventions\n\nkeep\n"
    out = strip_blocklisted_sections(doc)
    assert "PACKAGING" not in out
    assert "body to drop" not in out
    assert "Conventions" in out
    assert "keep" in out


def test_strip_preserves_non_blocklisted_sections_intact():
    doc = "## Conventions\n\nbody line 1\nbody line 2\n"
    out = strip_blocklisted_sections(doc)
    assert out.strip() == "## Conventions\n\nbody line 1\nbody line 2"


def test_strip_handles_no_heading_doc():
    doc = "Just prose, no headings at all.\n"
    out = strip_blocklisted_sections(doc)
    assert out == doc


def test_blocklist_contains_expected_terms():
    for term in [
        "toolchain",
        "environment",
        "commands",
        "packaging",
        "github action",
        "gitignore",
        "docs",
    ]:
        assert term in BLOCKLIST
