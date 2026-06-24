from __future__ import annotations

from superseded.review.prompts import JSON_FORMAT_INSTRUCTIONS, build_prompt


def test_new_sections_present():
    prompt = build_prompt(
        pass_name="security",
        diff="diff --git a/x.py b/x.py\n+bad",
        pr_description=None,
        file_context=None,
        memory_context=None,
        static_signals="### ruff\nF401 unused",
        usage_signals="### Usages of `foo`\nbar.py:5: foo()",
    )
    assert "### Static analysis signals" in prompt
    assert "F401 unused" in prompt
    assert "### Cross-file usages" in prompt
    assert "bar.py:5: foo()" in prompt


def test_new_sections_absent_when_none():
    prompt = build_prompt(
        pass_name="security",
        diff="diff --git a/x.py b/x.py\n+bad",
        pr_description=None,
        file_context=None,
        memory_context=None,
    )
    assert "No static analysis tools detected" in prompt
    assert "No usages retrieved" in prompt


def test_section_ordering():
    prompt = build_prompt(
        pass_name="security",
        diff="diff --git a/x.py b/x.py\n+bad",
        pr_description=None,
        file_context=None,
        memory_context=None,
        static_signals="ruff output",
        usage_signals="rg output",
    )
    diff_pos = prompt.index("### Changed Files (diff)")
    static_pos = prompt.index("### Static analysis signals")
    usage_pos = prompt.index("### Cross-file usages")
    file_pos = prompt.index("### File Context")
    assert diff_pos < static_pos < usage_pos < file_pos


def test_reasoning_in_json_format():
    assert "reasoning" in JSON_FORMAT_INSTRUCTIONS


def test_reasoning_rule_in_prompt():
    prompt = build_prompt(
        pass_name="correctness",
        diff="x",
        pr_description=None,
        file_context=None,
        memory_context=None,
    )
    assert "1-3 sentences" in prompt
    assert "evidence led you to flag" in prompt


def test_existing_sections_unchanged():
    prompt = build_prompt(
        pass_name="performance",
        diff="diff --git a/x.py b/x.py\n+old",
        pr_description="My PR",
        file_context="some context",
        memory_context="some memory",
    )
    assert "### PR Description" in prompt
    assert "My PR" in prompt
    assert "### Changed Files (diff)" in prompt
    assert "### File Context" in prompt
    assert "some context" in prompt
    assert "### Past Feedback" in prompt
    assert "some memory" in prompt
