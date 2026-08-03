from __future__ import annotations

import json

from superseded.models import Finding

MAX_RETRY_ERRORS_SHOWN = 8

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


SEVERITY_CALIBRATION = """
## Severity Calibration
Calibrate every finding against these anchors — `severity` must be one of exactly
`critical`, `important`, `suggestion`, `nit` (no other values are accepted):

- `critical` — exploitable vulnerability or correctness bug causing data loss /
  outage (e.g. SQL injection, auth bypass, secret logged in plaintext).
- `important` — likely bug or security weakness that should block merge
  (e.g. missing error handling, unchecked null deref, race condition).
- `suggestion` — meaningful improvement to clarity, correctness, or
  maintainability (e.g. unclear naming, redundant logic, fragile assertion).
- `nit` — subjective, trivial style preference (e.g. import ordering, whitespace).

When in doubt between two levels, pick the lower one.
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
    learned_context: str | None = None,
) -> str:
    instructions = PASS_INSTRUCTIONS.get(pass_name, "Review for issues.")
    pr_desc = pr_description or "No description provided."
    ctx = file_context or "No additional file context available."
    mem = memory_context or "No past feedback."
    static = static_signals or "No static analysis tools detected or available."
    usage = usage_signals or "No usages retrieved."
    conv = conventions_signals or "No project conventions discovered."
    spec = spec_signals or "No relevant specs/plans found."
    learned = (
        learned_context
        or "No learned guidelines yet. Guidelines form as feedback accumulates over multiple reviews."
    )

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

{SEVERITY_CALIBRATION}
## Context

### Project Conventions
{conv}

### Relevant Design Specs & Plans
{spec}

### Learned Review Guidelines
{learned}

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


def build_verify_prompt(
    findings: list[Finding],
    diff: str,
    file_context: str | None,
) -> str:
    """Build a verification prompt that asks the agent to re-examine merged findings.

    The agent receives the full diff, surrounding file context, and the merged
    findings JSON. It must return a verdict for each finding: ``keep`` (possibly
    with re-estimated severity/confidence) or ``drop`` (false positive).
    """
    ctx = file_context or "No additional file context available."

    findings_json = json.dumps(
        [
            {
                "id": f.id,
                "pass": f.pass_name,
                "severity": f.severity,
                "confidence": f.confidence,
                "file": f.file,
                "line": f.line,
                "title": f.title,
                "description": f.description,
            }
            for f in findings
        ],
        indent=2,
    )

    return f"""You are performing a final verify pass over the findings from a code review.

## Your Role
Verify each finding against the original diff and surrounding code. Your job is to catch
false positives and re-calibrate severity. Be skeptical: if the code already handles the
issue, the finding is wrong. Only drop a finding when the code clearly disproves it —
keeping noise is better than dropping a real bug.

{SEVERITY_CALIBRATION}

## Context

### Diff
{diff}

### File Context (surrounding code for changed files, +/-20 lines from changes)
{ctx}

### Merged Findings
{findings_json}

## Output Format
Return ONLY a JSON array. No explanation text before or after.

[
  {{
    "id": "correctness-a1b2c3d4e5f6",
    "action": "keep",
    "severity": "suggestion",
    "confidence": "low",
    "reason": "short justification"
  }},
  {{
    "id": "security-f7e8d9c0b1a2",
    "action": "drop",
    "reason": "The code already handles this case on line 42"
  }}
]

If you have no opinion on a finding, omit it from the array — it will be kept unchanged.
"""


def build_retry_prompt(original_prompt: str, errors: list[str]) -> str:
    """Append a corrective nudge so a pass whose output drifted from the schema
    (e.g. ``severity: "minor"``) can re-emit valid findings instead of being
    silently dropped. Bounded to the first ``MAX_RETRY_ERRORS_SHOWN`` errors so a
    badly malformed response doesn't blow up the prompt.
    """
    shown = errors[:MAX_RETRY_ERRORS_SHOWN]
    bullets = "\n".join(f"- {e}" for e in shown)
    extra = ""
    if len(errors) > len(shown):
        extra = f"\n(and {len(errors) - len(shown)} more)"
    return (
        f"{original_prompt}\n\n"
        "## Output Correction Required\n"
        f"Your previous response for this review contained {len(errors)} finding(s) "
        f"that failed validation:\n\n{bullets}{extra}\n\n"
        "Re-emit your findings as a valid JSON array. Each finding must use a "
        "`severity` of exactly `critical`, `important`, `suggestion`, or `nit`, "
        "and include `title`, `description`, `suggestion`, `file`, and `line`. "
        "Fix every rejected finding and re-include the findings that were already "
        "valid. Return ONLY the JSON array — no prose."
    )
