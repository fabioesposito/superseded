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


def test_finding_confidence_default():
    f = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        end_line=2,
        title="t",
        description="d",
        suggestion="s",
    )
    assert f.confidence == "high"


def test_finding_confidence_custom():
    f = Finding(
        pass_name="performance",
        severity="suggestion",
        file="a.py",
        line=1,
        end_line=2,
        title="t",
        description="d",
        suggestion="s",
        confidence="medium",
    )
    assert f.confidence == "medium"


def test_reasoning_defaults_empty():
    f = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        end_line=2,
        title="bad",
        description="desc",
        suggestion="fix",
    )
    assert f.reasoning == ""


def test_reasoning_roundtrip():
    f = Finding(
        pass_name="performance",
        severity="suggestion",
        file="b.py",
        line=10,
        end_line=12,
        title="slow",
        description="desc",
        suggestion="fix",
        reasoning="N+1 query in loop",
    )
    data = f.model_dump()
    f2 = Finding(**data)
    assert f2.reasoning == "N+1 query in loop"


def test_reasoning_does_not_affect_id():
    f1 = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        end_line=2,
        title="bad",
        description="d",
        suggestion="s",
        reasoning="because X",
    )
    f2 = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        end_line=2,
        title="bad",
        description="d",
        suggestion="s",
        reasoning="because Y",
    )
    assert f1.id == f2.id
