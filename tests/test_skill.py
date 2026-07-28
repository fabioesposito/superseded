from __future__ import annotations

from superseded.skill import SKILL_AGENTS, build_skill_text, skill_dir_for


def test_build_skill_text_has_frontmatter():
    text = build_skill_text()
    assert text.startswith("---\n")
    assert "name: superseded" in text
    assert "description:" in text
    # anti-rationalization + invocation must be present
    assert "Do not probe PATH" in text or "Do not verify superseded is installed" in text
    assert "superseded review --pr" in text


def test_skill_dir_for_each_agent():
    for name in SKILL_AGENTS:
        d = skill_dir_for(name)
        assert d.name == "superseded"
        assert d.parent.name == "skills"
    assert skill_dir_for("claude-code").parts[-3] == ".claude"
    assert skill_dir_for("opencode").parts[-3] == "opencode"
    assert skill_dir_for("codex").parts[-3] == ".agents"
