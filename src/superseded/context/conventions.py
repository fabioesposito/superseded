from __future__ import annotations

import logging
import re

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
