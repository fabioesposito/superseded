from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

CONVENTIONS_BUDGET = 4000

BLOCKLIST: list[str] = [
    "toolchain",
    "environment",
    "commands",
    "packaging",
    "github action",
    "gitignore",
    "docs",
]

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)

CONVENTION_FILES: list[str] = [
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    "CONTRIBUTING.md",
    ".editorconfig",
]


def strip_blocklisted_sections(text: str) -> str:
    """Drop markdown sections whose heading matches a BLOCKLIST term (substring, case-insensitive).

    A section is the heading line plus all lines until the next heading of any level.
    Non-markdown text (no headings) is returned unchanged.
    """
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return text

    kept: list[str] = []
    cursor = 0
    for i, m in enumerate(matches):
        heading_text = m.group(2).lower()
        section_start = m.start()
        section_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section = text[section_start:section_end]
        if any(term in heading_text for term in BLOCKLIST):
            if section_start > cursor:
                kept.append(text[cursor:section_start])
        else:
            kept.append(section)
        cursor = section_end
    if cursor < len(text):
        kept.append(text[cursor:])
    return "".join(kept)


def _read_optional(root: Path, filename: str) -> str | None:
    """Read a root-level file, with case-insensitive filename lookup on Linux."""
    path = root / filename
    if not path.exists():
        lower = filename.lower()
        found = False
        try:
            for entry in root.iterdir():
                if entry.is_file() and entry.name.lower() == lower:
                    path = entry
                    found = True
                    break
        except OSError:
            return None
        if not found:
            return None
    try:
        return path.read_text()
    except OSError as err:
        logger.warning("Could not read convention doc %s: %s", path, err)
        return None


def discover_conventions(root: Path) -> str | None:
    """Discover root-level convention docs, strip non-convention sections, concatenate, budget-cap."""
    blocks: list[str] = []
    for filename in CONVENTION_FILES:
        text = _read_optional(root, filename)
        if text is None:
            continue
        if filename.endswith(".md"):
            text = strip_blocklisted_sections(text)
        blocks.append(f"## {filename}\n{text.strip()}")

    if not blocks:
        return None

    aggregate = "\n\n".join(blocks)
    if len(aggregate) > CONVENTIONS_BUDGET:
        omitted = len(aggregate) - CONVENTIONS_BUDGET
        aggregate = aggregate[:CONVENTIONS_BUDGET] + (
            f"\n… ({omitted} more chars omitted by conventions budget)"
        )
    return aggregate
