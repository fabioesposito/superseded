from __future__ import annotations

from superseded.review.prompts import PASS_INSTRUCTIONS, build_prompt


def test_build_prompt_includes_diff():
    prompt = build_prompt(
        pass_name="security",
        diff="diff --git a/a.py ...",
        pr_description="Fix login bug",
        file_context=None,
        memory_context=None,
    )
    assert "security" in prompt.lower()
    assert "diff --git a/a.py" in prompt
    assert "Fix login bug" in prompt


def test_build_prompt_includes_memory():
    prompt = build_prompt(
        pass_name="style",
        diff="diff",
        pr_description=None,
        file_context=None,
        memory_context="Past dismissed: missing type hints — not enforced",
    )
    assert "Past dismissed" in prompt


def test_build_prompt_includes_file_context():
    prompt = build_prompt(
        pass_name="correctness",
        diff="diff",
        pr_description=None,
        file_context="def login():\n    pass",
        memory_context=None,
    )
    assert "def login():" in prompt


def test_all_passes_have_instructions():
    for name in ["security", "correctness", "performance", "style", "architecture"]:
        assert name in PASS_INSTRUCTIONS
        assert len(PASS_INSTRUCTIONS[name]) > 10
