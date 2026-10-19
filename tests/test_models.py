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


def test_finding_defaults_end_line_to_line():
    f = Finding(
        pass_name="security",
        severity="critical",
        file="src/auth.py",
        line=42,
        title="SQL injection",
        description="desc",
        suggestion="fix",
    )
    assert f.end_line == 42


def test_finding_keeps_explicit_end_line():
    f = Finding(
        pass_name="security",
        severity="critical",
        file="src/auth.py",
        line=42,
        end_line=50,
        title="SQL injection",
        description="desc",
        suggestion="fix",
    )
    assert f.end_line == 50


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


def test_finding_verification_fields_default():
    f = Finding(
        pass_name="security",
        severity="important",
        file="foo.py",
        line=42,
        title="SQL injection",
        description="User input in raw query",
        suggestion="Use parameterized queries",
    )
    assert f.verification is None
    assert f.verified_severity is None
    assert f.verification_reason is None


def test_finding_verification_dropped():
    f = Finding(
        pass_name="security",
        severity="important",
        file="foo.py",
        line=42,
        title="SQL injection",
        description="User input in raw query",
        suggestion="Use parameterized queries",
        verification="dropped",
        verification_reason="Code already sanitizes input on line 15",
    )
    assert f.verification == "dropped"
    assert f.verification_reason == "Code already sanitizes input on line 15"


def test_finding_verification_reestimate():
    f = Finding(
        pass_name="style",
        severity="important",
        file="bar.py",
        line=10,
        title="Unclear naming",
        description="Variable x is ambiguous",
        suggestion="Rename to user_count",
        verification="kept",
        verified_severity="suggestion",
    )
    assert f.verification == "kept"
    assert f.verified_severity == "suggestion"


def test_review_usage_defaults():
    from superseded.models import ReviewUsage

    u = ReviewUsage()
    assert u.prompt_tokens == 0
    assert u.completion_tokens == 0
    assert u.per_pass == {}


def test_review_usage_per_pass_dict():
    from superseded.models import ReviewUsage

    u = ReviewUsage(prompt_tokens=100, completion_tokens=50, per_pass={"security": (60, 30)})
    assert u.per_pass["security"] == (60, 30)


def test_review_result_has_usage_field():
    from superseded.models import ReviewResult, ReviewUsage

    r = ReviewResult()
    assert isinstance(r.usage, ReviewUsage)
    assert r.usage.prompt_tokens == 0


def _grounding_finding(**overrides):
    kwargs = dict(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        title="issue",
        description="d",
        suggestion="s",
    )
    kwargs.update(overrides)
    return Finding(**kwargs)


def test_finding_grounding_fields_default():
    f = _grounding_finding()
    assert f.evidence == ""
    assert f.evidence_verified is False
    assert f.pre_existing is False
    assert f.duplicate_of_static is False


def test_finding_evidence_roundtrip():
    f = _grounding_finding(evidence="+    return 2")
    data = f.model_dump()
    assert Finding(**data).evidence == "+    return 2"


def test_is_blocking_for_blocking_severities():
    assert _grounding_finding(severity="critical").is_blocking is True
    assert _grounding_finding(severity="important").is_blocking is True


def test_is_blocking_false_for_advisory_severities():
    assert _grounding_finding(severity="suggestion").is_blocking is False
    assert _grounding_finding(severity="nit").is_blocking is False


def test_is_blocking_ignores_pre_existing():
    # A finding outside the changed hunks must never block a PR.
    assert _grounding_finding(severity="critical", pre_existing=True).is_blocking is False


def test_fingerprint_ignores_line_number():
    f1 = _grounding_finding(file="a.py", line=10, title="SQL injection")
    f2 = _grounding_finding(file="a.py", line=99, title="SQL injection")
    assert f1.fingerprint == f2.fingerprint


def test_fingerprint_distinguishes_title():
    f1 = _grounding_finding(file="a.py", line=10, title="SQL injection")
    f2 = _grounding_finding(file="a.py", line=10, title="XSS")
    assert f1.fingerprint != f2.fingerprint


def test_review_result_convergence_defaults():
    r = ReviewResult()
    assert r.carried_findings == []
    assert r.round_number == 0
    assert r.at_round_cap is False
    assert r.suppressed_count == 0
    assert r.convergence_note == ""
