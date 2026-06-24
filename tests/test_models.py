from __future__ import annotations

from superseded.models import Finding, ReviewResult


def test_finding_creation():
    f = Finding(
        pass_name="security",
        severity="critical",
        file="src/auth.py",
        line=42,
        end_line=45,
        title="SQL injection",
        description="User input interpolated into SQL",
        suggestion="Use parameterized queries",
    )
    assert f.pass_name == "security"
    assert f.severity == "critical"
    assert f.file == "src/auth.py"
    assert f.line == 42


def test_finding_generates_id():
    f = Finding(
        pass_name="security",
        severity="critical",
        file="src/auth.py",
        line=42,
        end_line=45,
        title="SQL injection",
        description="desc",
        suggestion="fix",
    )
    assert f.id.startswith("security-")
    assert len(f.id) > 10


def test_review_result_from_findings():
    findings = [
        Finding(
            pass_name="security",
            severity="critical",
            file="a.py",
            line=1,
            end_line=2,
            title="t",
            description="d",
            suggestion="s",
        ),
        Finding(
            pass_name="style",
            severity="nit",
            file="b.py",
            line=5,
            end_line=5,
            title="t2",
            description="d2",
            suggestion="s2",
        ),
    ]
    result = ReviewResult(findings=findings)
    assert len(result.findings) == 2
    assert result.summary["critical"] == 1
    assert result.summary["nit"] == 1
