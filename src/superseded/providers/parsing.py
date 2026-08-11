from __future__ import annotations

import json
import re

_FENCED_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)
_ARRAY_RE = re.compile(r"\[\s*\{.*\}\s*\]", re.DOTALL)

MAX_FINDINGS_PER_PASS = 500


def parse_findings_json(raw: str, pass_name: str) -> list[dict]:
    """Extract a JSON array of finding dicts from `raw` model output.

    Handles three common shapes: a bare JSON array, a ```json fenced block,
    and an array embedded in prose. Returns [] when no parseable JSON array
    is present; the engine treats that as an empty pass (same behavior as
    before the provider refactor). Each returned dict gets `pass_name`
    injected. The result is capped at MAX_FINDINGS_PER_PASS items.
    """
    for candidate in _extract_candidates(raw):
        try:
            parsed = json.loads(candidate)
        except ValueError, TypeError:
            continue
        if isinstance(parsed, list):
            break
    else:
        return []
    items: list[dict] = []
    for el in parsed:
        if isinstance(el, dict):
            el["pass_name"] = pass_name
            items.append(el)
    return items[:MAX_FINDINGS_PER_PASS]


def _extract_candidates(raw: str) -> list[str]:
    """Collect parseable-array candidates in precedence order.

    Returns the array candidates to try: fenced ```json block, bare array,
    greedy regex match, then the balanced-bracket scan. The parse loop tries
    each with ``json.loads`` until one succeeds, so a garbage regex match
    (e.g. spanning two arrays) still falls through to the balanced scan.
    """
    candidates: list[str] = []
    # Try fenced ```json block first (most explicit).
    m = _FENCED_RE.search(raw)
    if m:
        inner = m.group(1).strip()
        if inner.startswith("["):
            candidates.append(inner)
    # Try bare/whitespace-trimmed JSON array.
    stripped = raw.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        candidates.append(stripped)
    # Fall back to an array embedded in prose.
    m = _ARRAY_RE.search(raw)
    if m:
        candidates.append(m.group(0))
    # Final fallback: a balanced-bracket scan that tolerates `[A] garbage [B]`
    # and `[garbage [valid]` shapes a single greedy regex cannot.
    candidates.append(_extract_balanced_array(raw))
    return candidates


def _extract_balanced_array(raw: str) -> str | None:
    """Scan for the first balanced [ ... ] array, string-literal aware.

    Tries each candidate left-to-right, returning the first that parses as
    a JSON array. Handles `[A] garbage [B]` and `[garbage [valid]` shapes
    that a single greedy regex cannot.
    """
    depth = 0
    start: int | None = None
    in_string = False
    escaped = False
    i = 0
    while i < len(raw):
        ch = raw[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "[":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0 and start is not None:
                    candidate = raw[start : i + 1]
                    try:
                        parsed = json.loads(candidate)
                    except ValueError, TypeError:
                        start = None
                        i += 1
                        continue
                    if isinstance(parsed, list):
                        return candidate
                    start = None
        i += 1
    return None
