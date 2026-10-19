from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, Field

from superseded.models import Finding, ReviewResult

if TYPE_CHECKING:
    from superseded.config import Config
    from superseded.providers import Provider


class ExpectedFinding(BaseModel):
    """A single finding the review is expected to produce (or must not)."""

    file: str
    line: int
    title_contains: str
    severity: str | None = None


class EvalCase(BaseModel):
    """One offline eval case: a diff, optional file context, and expected findings."""

    name: str
    diff: str
    files: dict[str, str] = Field(default_factory=dict)
    passes: list[str] | None = None
    expected: list[ExpectedFinding]
    forbidden: list[ExpectedFinding] = Field(default_factory=list)


class CaseResult(BaseModel):
    name: str
    passed: bool
    recall: float
    precision: float
    expected_count: int
    actual_count: int
    matched_count: int
    forbidden_hit: bool


class EvalReport(BaseModel):
    cases: list[CaseResult]
    passed: int
    total: int
    recall: float
    precision: float


def _load_file(path: Path) -> list[EvalCase]:
    data = yaml.safe_load(path.read_text())
    if data is None:
        return []
    if not isinstance(data, list):
        raise ValueError(f"eval cases file {path} must contain a top-level list of cases")
    return [EvalCase.model_validate(item) for item in data]


def load_cases(path: Path) -> list[EvalCase]:
    """Load and validate eval cases from a YAML file or a directory of YAML files.

    A directory's ``*.yaml``/``*.yml`` files are loaded in sorted order and their
    cases concatenated. Duplicate case names and empty corpora are rejected.
    """
    if path.is_dir():
        yaml_files = sorted(set(path.glob("*.yaml")) | set(path.glob("*.yml")))
        cases: list[EvalCase] = []
        for f in yaml_files:
            cases.extend(_load_file(f))
    else:
        cases = _load_file(path)

    if not cases:
        raise ValueError(f"no eval cases found in {path}")

    seen: set[str] = set()
    dupes: set[str] = set()
    for case in cases:
        if case.name in seen:
            dupes.add(case.name)
        seen.add(case.name)
    if dupes:
        raise ValueError(f"duplicate eval case name(s): {', '.join(sorted(dupes))}")

    return cases


def validate_case_lines(case: EvalCase) -> list[str]:
    """Validate that expected/forbidden line numbers fall inside the diff hunks.

    A live provider's only line-numbered reference is the diff, so an expected
    (or forbidden) finding must point at a line the diff's new-side hunks cover.
    Returns a list of human-readable problems (empty when the case is valid).
    """
    from superseded.diff import file_hunk_windows, parse_diff_files

    problems: list[str] = []
    diff_files = {entry["file"] for entry in parse_diff_files(case.diff)}
    windows = file_hunk_windows(case.diff)

    entries = [("expected", e) for e in case.expected] + [("forbidden", f) for f in case.forbidden]
    for kind, finding in entries:
        if finding.file not in diff_files:
            problems.append(f"{case.name}: {kind} file {finding.file!r} not present in diff")
            continue
        wins = windows.get(finding.file, [])
        if not any(start <= finding.line <= end for start, end in wins):
            problems.append(
                f"{case.name}: {kind} {finding.file}:{finding.line} outside new-side "
                f"hunk windows {wins}"
            )
    return problems


def _matches(finding: Finding, expected: ExpectedFinding) -> bool:
    if finding.file != expected.file:
        return False
    if abs(finding.line - expected.line) > 2:
        return False
    if expected.title_contains.lower() not in finding.title.lower():
        return False
    return expected.severity is None or finding.severity == expected.severity


def evaluate_case(case: EvalCase, result: ReviewResult) -> CaseResult:
    """Score one case against a ``ReviewResult``.

    A finding matches an expected entry when file, line (±2), and a
    case-insensitive title substring agree (plus exact severity when set). The
    greedy pass matches each expected entry and each finding at most once.
    """
    findings = result.findings
    used_finding = [False] * len(findings)
    matched_count = 0

    for expected in case.expected:
        for finding_i, finding in enumerate(findings):
            if used_finding[finding_i]:
                continue
            if _matches(finding, expected):
                used_finding[finding_i] = True
                matched_count += 1
                break

    forbidden_hit = any(
        any(_matches(finding, forbidden) for finding in findings) for forbidden in case.forbidden
    )

    expected_count = len(case.expected)
    actual_count = len(findings)
    recall = matched_count / expected_count if expected_count else 1.0
    precision = matched_count / actual_count if actual_count else 1.0

    return CaseResult(
        name=case.name,
        passed=recall == 1.0 and not forbidden_hit,
        recall=recall,
        precision=precision,
        expected_count=expected_count,
        actual_count=actual_count,
        matched_count=matched_count,
        forbidden_hit=forbidden_hit,
    )


def _build_file_context(files: dict[str, str]) -> str:
    return "\n\n".join(f"## {path}\n{content}" for path, content in files.items())


def run_cases(
    cases: list[EvalCase],
    provider: Provider,
    config: Config,
    *,
    model: str | None = None,
) -> EvalReport:
    """Run each case through a real ``ReviewEngine`` and score the aggregate."""
    from superseded.review.engine import ReviewEngine

    results: list[CaseResult] = []
    for case in cases:
        file_context = _build_file_context(case.files) or None
        engine = ReviewEngine(provider=provider, config=config)
        engine.model = model
        result = engine.review(
            diff=case.diff,
            file_context=file_context,
            passes=case.passes,
            timeout=600,
        )
        results.append(evaluate_case(case, result))

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    recall = sum(r.recall for r in results) / total if total else 0.0
    precision = sum(r.precision for r in results) / total if total else 0.0
    return EvalReport(
        cases=results,
        passed=passed,
        total=total,
        recall=recall,
        precision=precision,
    )
