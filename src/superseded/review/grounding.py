from __future__ import annotations

import re

from superseded.diff import file_hunk_windows
from superseded.models import Finding

_BLOCKING = frozenset({"critical", "important"})

# Matches ruff-style `path.py:10:1: E501`, mypy-style `path.py:10: error`,
# and gitleaks-style `  path.py:10 — secret`. The optional `:col` (ruff) is
# swallowed; the lookahead requires a separator so the line number is complete.
_STATIC_LOCATION_RE = re.compile(
    r"(?P<file>[^\s:]+):(?P<line>\d+)(?::\d+)?(?=\s|:|—|$)",
    re.MULTILINE,
)

# Strip a leading line-number prefix (`12: `) that file context prepends.
_CONTEXT_LINE_RE = re.compile(r"^\s*\d+:\s?")


def _line_in_windows(line: int, windows: list[tuple[int, int]]) -> bool:
    return any(start <= line <= end for start, end in windows)


def mark_pre_existing(findings: list[Finding], diff: str) -> list[Finding]:
    """Flag findings whose line falls outside the changed hunks of their file.

    An empty/whitespace diff cannot be validated, so every finding is left
    ``pre_existing=False`` (conservative: never block on an ungrounded finding).
    """
    if not diff or not diff.strip():
        return findings
    windows = file_hunk_windows(diff)
    for f in findings:
        wins = windows.get(f.file)
        if not wins or not _line_in_windows(f.line, wins):
            f.pre_existing = True
    return findings


def _parse_static_locations(static_signals: str) -> set[tuple[str, int]]:
    locations: set[tuple[str, int]] = set()
    for m in _STATIC_LOCATION_RE.finditer(static_signals):
        locations.add((m.group("file"), int(m.group("line"))))
    return locations


def filter_static_duplicates(findings: list[Finding], static_signals: str | None) -> list[Finding]:
    """Drop findings that static analysis already reported, unless blocking.

    A duplicate of a static signal at the same file/line is redundant for
    ``suggestion``/``nit`` findings (dropped), but a ``critical``/``important``
    finding is kept and flagged via ``duplicate_of_static`` so the human can
    see the AI corroborated a deterministic tool.
    """
    if not static_signals:
        return findings
    locations = _parse_static_locations(static_signals)
    if not locations:
        return findings
    kept: list[Finding] = []
    for f in findings:
        if (f.file, f.line) in locations:
            if f.severity in _BLOCKING:
                f.duplicate_of_static = True
                kept.append(f)
            continue
        kept.append(f)
    return kept


def _normalize(line: str) -> str:
    s = line.strip()
    if s[:1] in ("+", "-"):
        s = s[1:].lstrip()
    return " ".join(s.split())


def _build_corpus(diff: str, file_context: str | None) -> set[str]:
    corpus: set[str] = set()
    for line in diff.splitlines():
        n = _normalize(line)
        if n:
            corpus.add(n)
    if file_context:
        for line in file_context.splitlines():
            n = _normalize(_CONTEXT_LINE_RE.sub("", line))
            if n:
                corpus.add(n)
    return corpus


def _evidence_matches(evidence: str, corpus: set[str]) -> bool:
    for line in evidence.splitlines():
        n = _normalize(line)
        if n and n in corpus:
            return True
    return False


def validate_evidence(
    findings: list[Finding], diff: str, file_context: str | None
) -> list[Finding]:
    """Verify each finding's quoted evidence against the diff/file context.

    A finding whose ``evidence`` quotes a changed line (whitespace and leading
    ``+``/``-`` normalized) is marked ``evidence_verified``. Unverified
    ``suggestion``/``nit`` findings are dropped; unverified
    ``critical``/``important`` findings are kept but left unverified.
    """
    corpus = _build_corpus(diff, file_context)
    kept: list[Finding] = []
    for f in findings:
        if _evidence_matches(f.evidence, corpus):
            f.evidence_verified = True
            kept.append(f)
        elif f.severity in _BLOCKING:
            f.evidence_verified = False
            kept.append(f)
        # Unverified advisory findings are dropped.
    return kept
