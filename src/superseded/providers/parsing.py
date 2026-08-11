from __future__ import annotations

import json
import re

_FENCED_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)
_ARRAY_RE = re.compile(r"\[\s*\{.*\}\s*\]", re.DOTALL)


def parse_findings_json(raw: str, pass_name: str) -> list[dict]:
    """Extract a JSON array of finding dicts from `raw` model output.

    Handles three common shapes: a bare JSON array, a ```json fenced block,
    and an array embedded in prose. Returns [] for anything else so the
    engine's retry path can react to schema drift. Each returned dict gets
    `pass_name` injected.
    """
    candidate = _extract_array_text(raw)
    if candidate is None:
        return []
    try:
        parsed = json.loads(candidate)
    except ValueError, TypeError:
        return []
    if not isinstance(parsed, list):
        return []
    items: list[dict] = []
    for el in parsed:
        if isinstance(el, dict):
            el["pass_name"] = pass_name
            items.append(el)
    return items


def _extract_array_text(raw: str) -> str | None:
    # Try fenced ```json block first (most explicit).
    m = _FENCED_RE.search(raw)
    if m:
        inner = m.group(1).strip()
        if inner.startswith("["):
            return inner
    # Try bare/whitespace-trimmed JSON array.
    stripped = raw.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        return stripped
    # Fall back to an array embedded in prose.
    m = _ARRAY_RE.search(raw)
    if m:
        return m.group(0)
    return None
