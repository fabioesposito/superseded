from __future__ import annotations

from superseded.config import VerificationConfig
from superseded.verification import (
    VerificationEngine,
    parse_review_findings,
    parse_test_results,
    validate_artifact_sections,
)


class TestValidateArtifactSections:
    def test_all_sections_present(self):
        content = "## Problem\nWe need stuff.\n## Solution\nBuild it.\n## Requirements\nMust work."
        result = validate_artifact_sections(content, ["Problem", "Solution", "Requirements"])
        assert result == []

    def test_missing_section(self):
        content = "## Problem\nWe need stuff.\n## Solution\nBuild it."
        result = validate_artifact_sections(content, ["Problem", "Solution", "Requirements"])
        assert len(result) == 1
        assert "Requirements" in result[0]

    def test_multiple_missing(self):
        content = "## Problem\nWe need stuff."
        result = validate_artifact_sections(content, ["Problem", "Solution", "Requirements"])
        assert len(result) == 2

    def test_empty_content(self):
        result = validate_artifact_sections("", ["Problem"])
        assert len(result) == 1

    def test_no_required_sections(self):
        content = "## Whatever\nSome content."
        result = validate_artifact_sections(content, [])
        assert result == []

    def test_case_insensitive_match(self):
        content = "## problem\nWe need stuff.\n## SOLUTION\nBuild it."
        result = validate_artifact_sections(content, ["Problem", "Solution"])
        assert result == []


class TestParseReviewFindings:
    def test_critical_findings(self):
        output = "## Critical\n- SQL injection in auth.py\n- XSS in template.html\n## Important\n- Missing error handling"
        findings = parse_review_findings(output)
        assert findings["critical"] == 2
        assert findings["important"] == 1

    def test_no_findings(self):
        output = "Looks good. No issues found."
        findings = parse_review_findings(output)
        assert findings["critical"] == 0
        assert findings["important"] == 0

    def test_all_severities(self):
        output = "## Critical\n- Bug\n## Important\n- Issue\n## Nit\n- Style\n## FYI\n- Info"
        findings = parse_review_findings(output)
        assert findings["critical"] == 1
        assert findings["important"] == 1
        assert findings["nit"] == 1
        assert findings["fyi"] == 1

    def test_mixed_content_before_headings(self):
        output = "Here is my review.\n\n## Critical\n- Security hole\n\nSome explanation.\n## Important\n- Missing tests"
        findings = parse_review_findings(output)
        assert findings["critical"] == 1
        assert findings["important"] == 1


class TestParseTestResults:
    def test_pytest_output(self):
        output = "===== 45 passed, 3 failed in 12.5s ====="
        results = parse_test_results(output)
        assert results["passed"] == 45
        assert results["failed"] == 3

    def test_pytest_all_pass(self):
        output = "===== 50 passed in 8.2s ====="
        results = parse_test_results(output)
        assert results["passed"] == 50
        assert results["failed"] == 0

    def test_jest_output(self):
        output = "Tests: 2 failed, 10 passed, 12 total"
        results = parse_test_results(output)
        assert results["passed"] == 10
        assert results["failed"] == 2

    def test_go_test_output(self):
        output = "ok  \tmyapp/pkg\t0.5s\nFAIL\tmyapp/cmd\t1.2s"
        results = parse_test_results(output)
        assert results["passed"] == 1
        assert results["failed"] == 1

    def test_no_test_results(self):
        output = "Building project... done."
        results = parse_test_results(output)
        assert results["passed"] == 0
        assert results["failed"] == 0


class TestVerificationEngine:
    def test_no_verification_needed(self):
        engine = VerificationEngine()
        config = VerificationConfig()
        result = engine.verify("build", "Some output", {}, config)
        assert result.passed is True
        assert result.failures == []

    def test_spec_missing_sections(self):
        engine = VerificationEngine()
        config = VerificationConfig(required_sections=["Problem", "Solution", "Requirements"])
        artifacts = {"spec.md": "## Problem\nWe need stuff.\n## Solution\nBuild it."}
        result = engine.verify("spec", "", artifacts, config)
        assert result.passed is False
        assert any("Requirements" in f for f in result.failures)

    def test_spec_all_sections_present(self):
        engine = VerificationEngine()
        config = VerificationConfig(required_sections=["Problem", "Solution"])
        artifacts = {"spec.md": "## Problem\nWe need stuff.\n## Solution\nBuild it."}
        result = engine.verify("spec", "", artifacts, config)
        assert result.passed is True

    def test_review_critical_findings_block(self):
        engine = VerificationEngine()
        config = VerificationConfig(max_critical_findings=0)
        output = "## Critical\n- SQL injection\n## Important\n- Missing tests"
        result = engine.verify("review", output, {}, config)
        assert result.passed is False
        assert any("Critical" in f for f in result.failures)

    def test_review_within_threshold(self):
        engine = VerificationEngine()
        config = VerificationConfig(max_critical_findings=0, max_important_findings=5)
        output = "## Important\n- Issue 1\n- Issue 2"
        result = engine.verify("review", output, {}, config)
        assert result.passed is True

    def test_review_exceeds_important_threshold(self):
        engine = VerificationEngine()
        config = VerificationConfig(max_important_findings=1)
        output = "## Important\n- Issue 1\n- Issue 2"
        result = engine.verify("review", output, {}, config)
        assert result.passed is False
        assert any("Important" in f for f in result.failures)

    def test_verify_stage_test_failures(self):
        engine = VerificationEngine()
        config = VerificationConfig()
        output = "===== 5 passed, 2 failed in 3.0s ====="
        result = engine.verify("verify", output, {}, config)
        assert result.passed is False
        assert any("2 failed" in f for f in result.failures)

    def test_verify_stage_all_tests_pass(self):
        engine = VerificationEngine()
        config = VerificationConfig()
        output = "===== 50 passed in 8.2s ====="
        result = engine.verify("verify", output, {}, config)
        assert result.passed is True
