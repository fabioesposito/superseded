from __future__ import annotations

from superseded.review.prompts import JSON_FORMAT_INSTRUCTIONS, build_prompt, build_retry_prompt


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


def test_conventions_section_present_when_kwarg_non_empty():
    prompt = build_prompt(
        pass_name="security",
        diff="x",
        pr_description=None,
        file_context=None,
        memory_context=None,
        conventions_signals="## AGENTS.md\nuse double quotes",
    )
    assert "### Project Conventions" in prompt
    assert "use double quotes" in prompt


def test_conventions_placeholder_when_none():
    prompt = build_prompt(
        pass_name="security",
        diff="x",
        pr_description=None,
        file_context=None,
        memory_context=None,
    )
    assert "No project conventions discovered." in prompt


def test_spec_section_present_when_kwarg_non_empty():
    prompt = build_prompt(
        pass_name="correctness",
        diff="x",
        pr_description=None,
        file_context=None,
        memory_context=None,
        spec_signals="## docs/spec.md\nintent: do foo",
    )
    assert "### Relevant Design Specs & Plans" in prompt
    assert "intent: do foo" in prompt


def test_spec_placeholder_when_none():
    prompt = build_prompt(
        pass_name="correctness",
        diff="x",
        pr_description=None,
        file_context=None,
        memory_context=None,
    )
    assert "No relevant specs/plans found." in prompt


def test_conventions_and_spec_before_pr_description():
    prompt = build_prompt(
        pass_name="architecture",
        diff="x",
        pr_description="my PR",
        file_context=None,
        memory_context=None,
        conventions_signals="conv",
        spec_signals="spec",
    )
    conv_pos = prompt.index("### Project Conventions")
    spec_pos = prompt.index("### Relevant Design Specs & Plans")
    pr_pos = prompt.index("### PR Description")
    assert conv_pos < spec_pos < pr_pos


def test_enforcement_rules_present():
    prompt = build_prompt(
        pass_name="style",
        diff="x",
        pr_description=None,
        file_context=None,
        memory_context=None,
    )
    assert "Enforce the Project Conventions" in prompt
    assert "except deviations from the Project Conventions" in prompt
    assert "authoritative intent" in prompt


def test_old_sections_unchanged_when_new_kwargs_none():
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


def test_learned_context_section_present_when_kwarg_non_empty():
    prompt = build_prompt(
        pass_name="security",
        diff="x",
        pr_description=None,
        file_context=None,
        memory_context=None,
        learned_context="## Learned\navoid style nits in tests",
    )
    assert "### Learned Review Guidelines" in prompt
    assert "avoid style nits in tests" in prompt


def test_learned_context_placeholder_when_none():
    prompt = build_prompt(
        pass_name="security",
        diff="x",
        pr_description=None,
        file_context=None,
        memory_context=None,
    )
    assert "No learned guidelines yet" in prompt


def test_learned_context_ordering():
    prompt = build_prompt(
        pass_name="architecture",
        diff="x",
        pr_description="my PR",
        file_context=None,
        memory_context=None,
        conventions_signals="conv",
        spec_signals="spec",
        learned_context="learned stuff",
    )
    spec_pos = prompt.index("### Relevant Design Specs & Plans")
    learned_pos = prompt.index("### Learned Review Guidelines")
    pr_pos = prompt.index("### PR Description")
    assert spec_pos < learned_pos < pr_pos


def test_severity_calibration_section_present():
    """The prompt must include concrete anchors so the model calibrates
    severity instead of guessing (the benchmark saw `severity: "minor"` drift)."""
    prompt = build_prompt(
        pass_name="security",
        diff="x",
        pr_description=None,
        file_context=None,
        memory_context=None,
    )
    assert "Severity Calibration" in prompt


def test_severity_calibration_has_example_per_level():
    prompt = build_prompt(
        pass_name="correctness",
        diff="x",
        pr_description=None,
        file_context=None,
        memory_context=None,
    )
    # Each severity must carry at least one concrete example anchor.
    assert "SQL injection" in prompt
    assert "missing error handling" in prompt


def test_severity_calibration_lists_all_four_levels():
    prompt = build_prompt(
        pass_name="style",
        diff="x",
        pr_description=None,
        file_context=None,
        memory_context=None,
    )
    calibration_start = prompt.index("Severity Calibration")
    # The calibration block sits before the Context section.
    context_start = prompt.index("## Context")
    block = prompt[calibration_start:context_start]
    for level in ("critical", "important", "suggestion", "nit"):
        assert level in block


def test_build_retry_prompt_includes_errors_and_schema():
    """The corrective reprompt must surface the validation errors and re-state
    the accepted severity enum so the agent can self-correct."""
    retry = build_retry_prompt("ORIGINAL PROMPT", ["severity: not-a-severity", "missing title"])
    assert "ORIGINAL PROMPT" in retry
    assert "not-a-severity" in retry
    assert "missing title" in retry
    # Re-asserts the only valid severities.
    for level in ("critical", "important", "suggestion", "nit"):
        assert level in retry


def test_build_retry_prompt_truncates_many_errors():
    errors = [f"err {i}" for i in range(50)]
    retry = build_retry_prompt("p", errors)
    # Should not dump all 50 — keep it bounded.
    assert "err 0" in retry
    assert "err 49" not in retry
