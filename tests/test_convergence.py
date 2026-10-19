from __future__ import annotations

from superseded.models import Finding
from superseded.review.convergence import (
    ConvergencePlan,
    PriorFinding,
    plan_convergence,
    prior_from_rows,
)


def make_finding(**overrides):
    kwargs = dict(
        pass_name="security",
        severity="suggestion",
        file="a.py",
        line=3,
        title="issue",
        description="desc",
        suggestion="fix",
    )
    kwargs.update(overrides)
    return Finding(**kwargs)


def make_prior(finding=None, **overrides):
    kwargs = dict(
        fingerprint="fp",
        severity="suggestion",
        file="a.py",
        line=3,
        title="issue",
        comment_id=None,
        status="open",
        pass_name="correctness",
    )
    if finding is not None:
        kwargs["fingerprint"] = finding.fingerprint
        kwargs["file"] = finding.file
        kwargs["line"] = finding.line
        kwargs["title"] = finding.title
        kwargs["severity"] = finding.severity
        kwargs["pass_name"] = finding.pass_name
    kwargs.update(overrides)
    return PriorFinding(**kwargs)


# --- prior_from_rows -------------------------------------------------------


def test_prior_from_rows_maps_fields():
    prior = prior_from_rows(
        [
            {
                "fingerprint": "fp1",
                "severity": "critical",
                "file": "a.py",
                "line": 10,
                "title": "SQL injection",
                "comment_id": 42,
                "status": "open",
            }
        ]
    )
    assert len(prior) == 1
    p = prior[0]
    assert isinstance(p, PriorFinding)
    assert p.fingerprint == "fp1"
    assert p.severity == "critical"
    assert p.file == "a.py"
    assert p.line == 10
    assert p.title == "SQL injection"
    assert p.comment_id == 42
    assert p.status == "open"


def test_prior_from_rows_tolerates_missing_comment_id_and_line():
    prior = prior_from_rows(
        [
            {
                "fingerprint": "fp1",
                "severity": "critical",
                "file": "a.py",
                "title": "SQL injection",
                "status": "open",
            }
        ]
    )
    assert prior[0].comment_id is None
    assert prior[0].line == 0


def test_prior_from_rows_tolerates_none_comment_id_and_line():
    prior = prior_from_rows(
        [
            {
                "fingerprint": "fp1",
                "severity": "critical",
                "file": "a.py",
                "line": None,
                "title": "SQL injection",
                "comment_id": None,
                "status": "open",
            }
        ]
    )
    assert prior[0].comment_id is None
    assert prior[0].line == 0


def test_prior_from_rows_reads_pass_name():
    prior = prior_from_rows(
        [
            {
                "fingerprint": "fp1",
                "severity": "critical",
                "file": "a.py",
                "line": 10,
                "title": "SQL injection",
                "comment_id": 42,
                "status": "open",
                "pass": "security",
            }
        ]
    )
    assert prior[0].pass_name == "security"


def test_prior_from_rows_defaults_pass_name():
    prior = prior_from_rows(
        [
            {
                "fingerprint": "fp1",
                "severity": "critical",
                "file": "a.py",
                "line": 10,
                "title": "SQL injection",
                "comment_id": 42,
                "status": "open",
            }
        ]
    )
    assert prior[0].pass_name == "correctness"


# --- prior-status dedupe rules --------------------------------------------


def test_open_prior_suppresses_new_finding():
    f = make_finding(severity="important")
    plan = plan_convergence([f], [make_prior(f, status="open")], round_number=1)
    assert plan.findings_to_post == []
    assert plan.suppressed_count == 1


def test_dismissed_prior_suppresses_new_finding():
    f = make_finding()
    plan = plan_convergence([f], [make_prior(f, status="dismissed")], round_number=1)
    assert plan.findings_to_post == []
    assert plan.suppressed_count == 1


def test_resolved_prior_reposts_finding():
    f = make_finding(severity="important")
    plan = plan_convergence([f], [make_prior(f, status="resolved")], round_number=1)
    assert plan.findings_to_post == [f]
    assert plan.suppressed_count == 0


def test_no_prior_posts_finding():
    f = make_finding()
    plan = plan_convergence([f], [], round_number=1)
    assert plan.findings_to_post == [f]
    assert plan.suppressed_count == 0


def test_line_shifted_duplicate_is_suppressed():
    f1 = make_finding(file="a.py", line=10, title="SQL injection")
    f2 = make_finding(file="a.py", line=200, title="SQL injection")
    assert f1.fingerprint == f2.fingerprint
    plan = plan_convergence([f2], [make_prior(f1, status="open")], round_number=1)
    assert plan.findings_to_post == []
    assert plan.suppressed_count == 1


# --- carried findings ------------------------------------------------------


def test_carried_keeps_open_blocking_prior():
    crit = make_finding(severity="critical", title="crit")
    imp = make_finding(severity="important", title="imp")
    prior = [make_prior(crit, status="open"), make_prior(imp, status="open")]
    plan = plan_convergence([], prior, round_number=1)
    assert {p.title for p in plan.carried_findings} == {"crit", "imp"}


def test_carried_excludes_advisory_and_non_open():
    sugg = make_finding(severity="suggestion", title="sugg")
    nit = make_finding(severity="nit", title="nit")
    res = make_finding(severity="critical", title="res")
    dism = make_finding(severity="important", title="dism")
    prior = [
        make_prior(sugg, status="open"),
        make_prior(nit, status="open"),
        make_prior(res, status="resolved"),
        make_prior(dism, status="dismissed"),
    ]
    plan = plan_convergence([], prior, round_number=1)
    assert plan.carried_findings == []


# --- round cap -------------------------------------------------------------


def test_round_cap_suppresses_non_critical():
    crit = make_finding(severity="critical", title="crit")
    imp = make_finding(severity="important", title="imp")
    sugg = make_finding(severity="suggestion", title="sugg")
    nit = make_finding(severity="nit", title="nit")
    plan = plan_convergence([crit, imp, sugg, nit], [], round_number=4, max_rounds=3)
    assert plan.at_round_cap is True
    assert [f.title for f in plan.findings_to_post] == ["crit"]
    assert plan.suppressed_count == 3


def test_round_cap_not_active_within_rounds():
    imp = make_finding(severity="important")
    plan = plan_convergence([imp], [], round_number=3, max_rounds=3)
    assert plan.at_round_cap is False
    assert plan.findings_to_post == [imp]
    assert plan.suppressed_count == 0


def test_round_cap_does_not_suppress_reappeared_critical():
    crit = make_finding(severity="critical")
    plan = plan_convergence(
        [crit], [make_prior(crit, status="resolved")], round_number=4, max_rounds=3
    )
    assert plan.at_round_cap is True
    assert plan.findings_to_post == [crit]
    assert plan.suppressed_count == 0


# --- note / fields / immutability ------------------------------------------


def test_note_describes_suppression_and_carried():
    dup = make_finding(severity="suggestion")
    fresh = make_finding(severity="suggestion", title="other")
    crit = make_finding(severity="critical", title="crit")
    prior = [make_prior(dup, status="open"), make_prior(crit, status="open")]
    plan = plan_convergence([dup, fresh], prior, round_number=1)
    assert plan.suppressed_count == 1
    assert plan.findings_to_post == [fresh]
    assert "duplicate" in plan.note
    assert "still open from earlier rounds" in plan.note


def test_round_cap_note():
    imp = make_finding(severity="important")
    plan = plan_convergence([imp], [], round_number=4, max_rounds=3)
    assert "round cap" in plan.note


def test_note_empty_when_nothing_notable():
    f = make_finding()
    plan = plan_convergence([f], [], round_number=1)
    assert plan.note == ""


def test_plan_carries_round_number():
    f = make_finding()
    plan = plan_convergence([f], [], round_number=2)
    assert plan.round_number == 2
    assert isinstance(plan, ConvergencePlan)


def test_does_not_mutate_inputs():
    f = make_finding()
    prior = [make_prior(f, status="open")]
    findings = [f]
    plan_convergence(findings, prior, round_number=1)
    assert findings == [f]
    assert prior[0].status == "open"
