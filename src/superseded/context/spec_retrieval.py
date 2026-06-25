from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from superseded.diff import parse_diff_files

logger = logging.getLogger(__name__)

SPEC_BUDGET = 6000

_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")
_SUFFIX_RE = re.compile(r"-(?:design|implementation|plan)$")

SPEC_GLOBS: list[str] = [
    "docs/superseded/specs/*.md",
    "docs/superseded/plans/*.md",
    ".opencode/skills/**/*.md",
    ".agents/skills/**/*.md",
    "skills/**/*.md",
]


def derive_slug(filename: str) -> str:
    """Derive a lowercase slug from a spec/plan/skill filename.

    Strips a leading YYYY-MM-DD- date prefix and a trailing -design/-implementation/-plan suffix,
    plus the .md extension. For skill files (no date prefix), the slug is the filename stem.
    """
    stem = Path(filename).stem
    stem = _DATE_PREFIX_RE.sub("", stem)
    stem = _SUFFIX_RE.sub("", stem)
    return stem.lower()


def _candidate_files(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for pattern in SPEC_GLOBS:
        candidates.extend(root.glob(pattern))
    return [c for c in candidates if c.is_file()]


def _slug_in_paths(slug: str, changed_paths: list[str]) -> bool:
    slug_l = slug.lower()
    for p in changed_paths:
        parts = [part.lower() for part in p.split("/")]
        if slug_l in parts:
            return True
    return False


def _body_mentions_paths(body: str, patterns: list[str]) -> bool:
    if not patterns:
        return False
    try:
        cmd: list[str] = ["rg", "--fixed-strings", "-q"]
        for p in patterns:
            cmd.extend(["-e", p])
        result = subprocess.run(
            cmd,
            input=body,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        logger.warning("ripgrep not on PATH, skipping spec retrieval")
        raise
    except subprocess.TimeoutExpired:
        logger.warning("ripgrep timed out during spec retrieval, skipping")
        return False
    return result.returncode == 0


def _is_relevant(doc_path: Path, body: str, slug: str, changed_paths: list[str]) -> bool:
    if _slug_in_paths(slug, changed_paths):
        return True
    patterns = list({*changed_paths, *[Path(p).name for p in changed_paths]})
    try:
        return _body_mentions_paths(body, patterns)
    except FileNotFoundError:
        raise


def discover_repo_specs(diff: str, root: Path) -> str | None:
    """Discover specs/plans/skills relevant to the diff, concatenate, budget-cap."""
    entries = parse_diff_files(diff)
    changed_paths = [e["file"] for e in entries]
    if not changed_paths:
        return None

    candidates = _candidate_files(root)
    if not candidates:
        return None

    relevant: list[tuple[float, str, str]] = []
    for path in candidates:
        try:
            body = path.read_text()
        except OSError as err:
            logger.warning("Could not read spec/plan %s: %s", path, err)
            continue
        slug = derive_slug(path.name)
        try:
            if not _is_relevant(path, body, slug, changed_paths):
                continue
        except FileNotFoundError:
            return None
        rel = str(path.relative_to(root))
        relevant.append((path.stat().st_mtime, rel, body))

    if not relevant:
        return None

    specs = sorted(
        [(mt, rel, body) for mt, rel, body in relevant if "/specs/" in rel.replace("\\", "/")],
        key=lambda t: t[0],
        reverse=True,
    )
    plans = sorted(
        [(mt, rel, body) for mt, rel, body in relevant if "/plans/" in rel.replace("\\", "/")],
        key=lambda t: t[0],
        reverse=True,
    )
    skills = sorted(
        [
            (mt, rel, body)
            for mt, rel, body in relevant
            if "/specs/" not in rel.replace("\\", "/") and "/plans/" not in rel.replace("\\", "/")
        ],
        key=lambda t: t[0],
        reverse=True,
    )

    blocks: list[str] = []
    for _mt, rel, body in [*specs, *plans, *skills]:
        blocks.append(f"## {rel}\n{body.strip()}")

    aggregate = "\n\n".join(blocks)
    if len(aggregate) > SPEC_BUDGET:
        omitted = len(aggregate) - SPEC_BUDGET
        aggregate = aggregate[:SPEC_BUDGET] + (
            f"\n\u2026 ({omitted} more chars omitted by spec-retrieval budget)"
        )
    return aggregate
