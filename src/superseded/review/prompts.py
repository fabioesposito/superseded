from __future__ import annotations

PASS_INSTRUCTIONS: dict[str, str] = {
    "security": (
        "Focus on: injection vulnerabilities, auth bypass, secret exposure, "
        "unsafe deserialization, path traversal, SSRF, XSS. Think like an attacker."
    ),
    "correctness": (
        "Focus on: logic errors, off-by-one, null/undefined handling, race conditions, "
        "error handling gaps, incorrect assumptions. Does the code match the PR description?"
    ),
    "performance": (
        "Focus on: N+1 queries, unnecessary allocations, blocking I/O in async paths, "
        "O(n²) where O(n) is possible, missing caching opportunities."
    ),
    "style": (
        "Focus on: unclear naming, dead code, overly complex logic, inconsistent patterns "
        "with the rest of the codebase, missing type hints."
    ),
    "architecture": (
        "Focus on: separation of concerns, API contract changes, dependency direction, "
        "coupling between modules, public interface changes."
    ),
}

JSON_FORMAT_INSTRUCTIONS = """
## Output Format
Return ONLY a JSON array. No explanation text before or after.

[
  {
    "severity": "critical|important|suggestion|nit",
    "confidence": "high|medium|low",
    "file": "path/to/file.py",
    "line": 42,
    "end_line": 45,
    "title": "Short description",
    "description": "Detailed explanation of the issue",
    "suggestion": "Code fix or suggestion",
    "reasoning": "1-3 sentences explaining what evidence led to this finding."
  }
]

If no issues found, return: []
"""


def build_prompt(
    pass_name: str,
    diff: str,
    pr_description: str | None,
    file_context: str | None,
    memory_context: str | None,
    static_signals: str | None = None,
    usage_signals: str | None = None,
    conventions_signals: str | None = None,
    spec_signals: str | None = None,
) -> str:
    instructions = PASS_INSTRUCTIONS.get(pass_name, "Review for issues.")
    pr_desc = pr_description or "No description provided."
    ctx = file_context or "No additional file context available."
    mem = memory_context or "No past feedback."
    static = static_signals or "No static analysis tools detected or available."
    usage = usage_signals or "No usages retrieved."
    conv = conventions_signals or "No project conventions discovered."
    spec = spec_signals or "No relevant specs/plans found."

    return f"""You are performing a {pass_name} code review.

## Your Role
{instructions}

## Rules
- Only report genuine issues, not style preferences — except deviations from the Project Conventions below, which are reportable.
- Be specific: cite exact file, line numbers, and code
- Provide actionable suggestions with code examples
- Do NOT report issues in unchanged code unless directly related to the change
- If there are no issues in this category, return an empty array
- For each finding, briefly (1-3 sentences) explain what evidence led you to flag it
- Enforce the Project Conventions listed below: flag deviations as findings. Use severity `nit`/`suggestion` by default; use `important` only when the deviation breaks correctness or security. Do not flag code that conforms to the conventions.
- Use the Relevant Design Specs & Plans as authoritative intent. If changed code contradicts a spec, flag it at severity `important` or higher, citing the spec path.

## Context

### Project Conventions
{conv}

### Relevant Design Specs & Plans
{spec}

### PR Description
{pr_desc}

### Changed Files (diff)
{diff}

### Static analysis signals (run before AI; deterministic)
{static}

### Cross-file usages (callers of changed symbols, ±3 lines)
{usage}

### File Context (surrounding code for changed files, ±20 lines from changes)
{ctx}

### Past Feedback (findings dismissed by humans — avoid similar)
{mem}

{JSON_FORMAT_INSTRUCTIONS}"""
