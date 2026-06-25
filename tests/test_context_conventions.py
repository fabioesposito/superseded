from __future__ import annotations

from superseded.context.conventions import (
    BLOCKLIST,
    CONVENTIONS_BUDGET,
    discover_conventions,
    strip_blocklisted_sections,
)


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


# --- discover_conventions tests ---


def test_discover_returns_none_when_no_docs(tmp_path):
    assert discover_conventions(tmp_path) is None


def test_discover_finds_agents_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text("## Conventions\n\nuse double quotes\n")
    out = discover_conventions(tmp_path)
    assert out is not None
    assert "## AGENTS.md" in out
    assert "use double quotes" in out


def test_discover_case_insensitive_filename(tmp_path):
    (tmp_path / "agents.md").write_text("## Conventions\n\nkeep\n")
    out = discover_conventions(tmp_path)
    assert out is not None
    assert "keep" in out


def test_discover_strips_blocklisted_sections(tmp_path):
    (tmp_path / "AGENTS.md").write_text(
        "## Conventions\n\nkeep this\n\n## Toolchain & environment\n\ndrop this\n"
    )
    out = discover_conventions(tmp_path)
    assert "keep this" in out
    assert "Toolchain" not in out
    assert "drop this" not in out


def test_discover_editorconfig_injected_whole(tmp_path):
    (tmp_path / ".editorconfig").write_text("root = true\n[*]\nindent_style = space\n")
    out = discover_conventions(tmp_path)
    assert out is not None
    assert "## .editorconfig" in out
    assert "indent_style = space" in out


def test_discover_concatenation_order(tmp_path):
    (tmp_path / "AGENTS.md").write_text("agents body\n")
    (tmp_path / "CLAUDE.md").write_text("claude body\n")
    (tmp_path / "GEMINI.md").write_text("gemini body\n")
    (tmp_path / "CONTRIBUTING.md").write_text("contributing body\n")
    (tmp_path / ".editorconfig").write_text("editorconfig body\n")
    out = discover_conventions(tmp_path)
    assert out is not None
    assert out.index("agents body") < out.index("claude body")
    assert out.index("claude body") < out.index("gemini body")
    assert out.index("gemini body") < out.index("contributing body")
    assert out.index("contributing body") < out.index("editorconfig body")


def test_discover_budget_truncation_tail(tmp_path):
    (tmp_path / "AGENTS.md").write_text("x" * (CONVENTIONS_BUDGET + 500))
    out = discover_conventions(tmp_path)
    assert out is not None
    assert "omitted by conventions budget" in out
    assert len(out) <= CONVENTIONS_BUDGET + 200


def test_discover_skips_unreadable_doc(tmp_path, caplog):
    (tmp_path / "AGENTS.md").write_text("## Conventions\nkeep\n")
    (tmp_path / "AGENTS.md").chmod(0o000)
    try:
        with caplog.at_level("WARNING"):
            out = discover_conventions(tmp_path)
    finally:
        (tmp_path / "AGENTS.md").chmod(0o644)
    assert out is None
