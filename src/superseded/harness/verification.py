from __future__ import annotations

import re
from dataclasses import dataclass, field

from superseded.config import VerificationConfig


@dataclass
class VerificationResult:
    passed: bool
    failures: list[str] = field(default_factory=list)


def validate_artifact_sections(content: str, required_sections: list[str]) -> list[str]:
    """Return list of missing section error messages. Empty means all present."""
    if not required_sections:
        return []
    missing = []
    content_lower = content.lower()
    for section in required_sections:
        pattern = rf"^##\s+{re.escape(section.lower())}\s*$"
        if not re.search(pattern, content_lower, re.MULTILINE):
            missing.append(f"Missing required section: ## {section}")
    return missing


def parse_review_findings(output: str) -> dict[str, int]:
    """Parse review output for severity-labeled findings."""
    severities = ["critical", "important", "nit", "fyi"]
    counts: dict[str, int] = {s: 0 for s in severities}

    for severity in severities:
        pattern = rf"^##\s+{severity}\s*$"
        matches = list(re.finditer(pattern, output, re.MULTILINE | re.IGNORECASE))
        if not matches:
            continue
        for match in matches:
            section_start = match.start()
            next_heading = re.search(r"^##\s+", output[section_start + 1 :], re.MULTILINE)
            if next_heading:
                section_text = output[section_start : section_start + 1 + next_heading.start()]
            else:
                section_text = output[section_start:]
            bullet_count = len(re.findall(r"^[-*]\s+", section_text, re.MULTILINE))
            counts[severity] += max(bullet_count, 1) if bullet_count > 0 else 0

    return counts


def parse_test_results(output: str) -> dict[str, int]:
    """Parse test output for pass/fail counts. Supports pytest, jest, go test."""
    result = {"passed": 0, "failed": 0}

    # pytest: "===== 45 passed, 3 failed in 12.5s ====="
    pytest_match = re.search(r"=+\s+(\d+)\s+passed(?:,\s+(\d+)\s+failed)?\s+in", output)
    if pytest_match:
        result["passed"] = int(pytest_match.group(1))
        if pytest_match.group(2):
            result["failed"] = int(pytest_match.group(2))
        return result

    # jest: "Tests: 2 failed, 10 passed, 12 total"
    jest_match = re.search(r"Tests:\s+(?:(\d+)\s+failed,\s+)?(\d+)\s+passed", output)
    if jest_match:
        result["failed"] = int(jest_match.group(1) or 0)
        result["passed"] = int(jest_match.group(2))
        return result

    # go test: "ok  \tpkg\t0.5s" / "FAIL\tpkg\t1.2s"
    ok_count = len(re.findall(r"^ok\s+", output, re.MULTILINE))
    fail_count = len(re.findall(r"^FAIL\s+", output, re.MULTILINE))
    if ok_count or fail_count:
        result["passed"] = ok_count
        result["failed"] = fail_count
        return result

    return result


class VerificationEngine:
    """Validates stage outputs against configurable criteria."""

    def verify(
        self,
        stage: str,
        output: str,
        artifacts: dict[str, str],
        config: VerificationConfig,
    ) -> VerificationResult:
        """Run verification for a stage. Returns VerificationResult."""
        failures: list[str] = []

        # Artifact section validation (SPEC, PLAN)
        if stage in ("spec", "plan") and config.required_sections:
            artifact_key = f"{stage}.md"
            content = artifacts.get(artifact_key, "")
            missing = validate_artifact_sections(content, config.required_sections)
            failures.extend(missing)

        # Review severity parsing (REVIEW)
        if stage == "review":
            findings = parse_review_findings(output)
            if findings["critical"] > config.max_critical_findings:
                failures.append(
                    f"Found {findings['critical']} Critical findings "
                    f"(max: {config.max_critical_findings}). "
                    f"Critical findings block merge."
                )
            if findings["important"] > config.max_important_findings:
                failures.append(
                    f"Found {findings['important']} Important findings "
                    f"(max: {config.max_important_findings})."
                )

        # Test result parsing (VERIFY)
        if stage == "verify":
            test_results = parse_test_results(output)
            if test_results["failed"] > 0:
                failures.append(
                    f"Tests failed: {test_results['failed']} failed, "
                    f"{test_results['passed']} passed."
                )

        return VerificationResult(
            passed=len(failures) == 0,
            failures=failures,
        )

    def format_errors_for_retry(self, result: VerificationResult) -> str:
        """Format verification failures as structured error text for retry prompt."""
        if result.passed:
            return ""
        lines = ["The previous attempt failed verification. Fix these specific issues:"]
        for i, failure in enumerate(result.failures, 1):
            lines.append(f"  {i}. {failure}")
        return "\n".join(lines)
