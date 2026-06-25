from __future__ import annotations

import logging
import re
from pathlib import Path

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
