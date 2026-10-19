from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from superseded.cli import cli
from superseded.config import Config
from superseded.eval.runner import (
    CaseResult,
    EvalCase,
    ExpectedFinding,
    evaluate_case,
    load_cases,
    run_cases,
)
from superseded.models import Finding, ReviewResult
from superseded.providers import ProviderResponse

CORPUS = Path(__file__).parent / "evals" / "cases.yaml"


def _finding(
    file: str = "a.py",
    line: int = 10,
    title: str = "SQL injection",
    severity: str = "suggestion",
) -> Finding:
    return Finding(
        pass_name="security",
        severity=severity,
        file=file,
        line=line,
        title=title,
        description="d",
        suggestion="s",
    )


def _result(*findings: Finding) -> ReviewResult:
    return ReviewResult(findings=list(findings))


def test_corpus_has_20_unique_cases_and_all_categories():
    cases = load_cases(CORPUS)
    names = [c.name for c in cases]
    assert len(cases) == 20
    assert len(set(names)) == 20

    # Each category is represented (security, correctness, performance,
    # style/architecture, and the clean/no-finding cases).
    representatives = {
        "sql-injection",  # security
        "off-by-one",  # correctness
        "n-plus-one-query",  # performance
        "dead-code",  # style
        "circular-dependency",  # architecture
        "clean-refactor",  # clean/no-finding
    }
    assert representatives <= set(names)

    # The clean cases must have no expected findings but a forbidden entry.
    clean = {c.name: c for c in cases if c.name.startswith("clean-")}
    assert len(clean) == 2
    for c in clean.values():
        assert c.expected == []
        assert c.forbidden


def test_evaluate_exact_match():
    case = EvalCase(
        name="c",
        diff="d",
        expected=[
            ExpectedFinding(
                file="a.py", line=10, title_contains="SQL injection", severity="critical"
            )
        ],
    )
    cr = evaluate_case(
        case, _result(_finding(line=10, title="Possible SQL injection", severity="critical"))
    )
    assert cr.passed is True
    assert cr.recall == 1.0
    assert cr.precision == 1.0
    assert cr.matched_count == 1
    assert cr.forbidden_hit is False


def test_evaluate_line_tolerance_plus_minus_two():
    case = EvalCase(
        name="c", diff="d", expected=[ExpectedFinding(file="a.py", line=10, title_contains="SQL")]
    )
    assert evaluate_case(case, _result(_finding(line=12))).matched_count == 1
    assert evaluate_case(case, _result(_finding(line=8))).matched_count == 1
    # Outside the ±2 window.
    assert evaluate_case(case, _result(_finding(line=13))).matched_count == 0


def test_evaluate_title_match_is_case_insensitive():
    case = EvalCase(
        name="c",
        diff="d",
        expected=[ExpectedFinding(file="a.py", line=10, title_contains="sql injection")],
    )
    assert evaluate_case(case, _result(_finding(title="SQL Injection Risk"))).matched_count == 1


def test_evaluate_severity_mismatch_does_not_match():
    case = EvalCase(
        name="c",
        diff="d",
        expected=[
            ExpectedFinding(
                file="a.py", line=10, title_contains="SQL injection", severity="critical"
            )
        ],
    )
    cr = evaluate_case(case, _result(_finding(title="SQL injection", severity="suggestion")))
    assert cr.matched_count == 0
    assert cr.passed is False


def test_evaluate_forbidden_hit_fails_case():
    case = EvalCase(
        name="c",
        diff="d",
        expected=[],
        forbidden=[ExpectedFinding(file="a.py", line=10, title_contains="SQL injection")],
    )
    cr = evaluate_case(case, _result(_finding(title="SQL injection")))
    assert cr.forbidden_hit is True
    assert cr.passed is False


def test_evaluate_each_finding_matches_at_most_one_expected():
    case = EvalCase(
        name="c",
        diff="d",
        expected=[
            ExpectedFinding(file="a.py", line=10, title_contains="SQL"),
            ExpectedFinding(file="a.py", line=10, title_contains="injection"),
        ],
    )
    # A single finding matches both substrings, but can only be claimed once.
    cr = evaluate_case(case, _result(_finding(title="SQL injection")))
    assert cr.matched_count == 1
    assert cr.recall == 0.5
    assert cr.precision == 1.0


def test_evaluate_empty_expected_recall_is_one():
    case = EvalCase(name="c", diff="d", expected=[])
    cr = evaluate_case(case, _result())
    assert cr.recall == 1.0
    assert cr.precision == 1.0
    assert cr.passed is True


def test_eval_dry_run_loads_and_prints_20():
    runner = CliRunner()
    result = runner.invoke(cli, ["eval", str(CORPUS), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "20 eval cases loaded" in result.output


def test_run_cases_with_fake_provider():
    class FakeProvider:
        name = "fake"

        def complete(
            self, prompt, *, model=None, timeout=600.0, temperature=0.0, reasoning_effort=None
        ):
            return ProviderResponse(
                content=json.dumps(
                    [
                        {
                            "file": "app.py",
                            "line": 12,
                            "title": "SQL injection risk",
                            "severity": "critical",
                            "description": "unsafe string formatting",
                            "suggestion": "use parameterized queries",
                            "evidence": 'query = f"SELECT * FROM users WHERE id = {user_id}"',
                        }
                    ]
                )
            )

    case = EvalCase(
        name="sql",
        diff=(
            "diff --git a/app.py b/app.py\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -10,6 +10,8 @@ def get_user():\n"
            '+    query = f"SELECT * FROM users WHERE id = {user_id}"\n'
        ),
        files={
            "app.py": 'def get_user():\n    query = f"SELECT * FROM users WHERE id = {user_id}"'
        },
        passes=["security"],
        expected=[
            ExpectedFinding(
                file="app.py", line=12, title_contains="SQL injection", severity="critical"
            )
        ],
    )

    report = run_cases([case], FakeProvider(), Config(verify=False))

    assert report.total == 1
    assert report.passed == 1
    assert report.recall == 1.0
    assert report.precision == 1.0
    case_result = report.cases[0]
    assert isinstance(case_result, CaseResult)
    assert case_result.passed is True
    assert case_result.matched_count == 1
