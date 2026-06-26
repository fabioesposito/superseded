from __future__ import annotations

import json
import re

JSON_ARRAY_RE = re.compile(r"\[\s*\{.*?\}\s*\]", re.DOTALL)


def _extract_first_balanced_array(raw: str, start: int = 0) -> tuple[str, int] | None:
    """Return (text, end_index) of the first balanced ``[...]`` after *start*.

    Respects JSON string literals (with escapes) so a ``}]`` substring inside
    a string value does not prematurely close the scan. Returns ``None`` if no
    balanced array starts at or after *start*.
    """
    n = len(raw)
    i = start
    while i < n:
        # Find the next '[' that could begin an array.
        open_idx = raw.find("[", i)
        if open_idx == -1:
            return None
        depth = 0
        in_str = False
        esc = False
        j = open_idx
        end = -1
        while j < n:
            ch = raw[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        end = j
                        break
            j += 1
        if end != -1:
            return raw[open_idx : end + 1], end + 1
        # No closer for this '['; advance past it and keep scanning.
        i = open_idx + 1
    return None


def extract_json_array(raw: str) -> list[dict] | None:
    # Prefer the cheap regex match first: most agents emit a clean JSON array
    # and the regex is allocation-free. If it decodes, we are done.
    match = JSON_ARRAY_RE.search(raw)
    if match:
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            return data

    # Fall back to a bracket-aware scan that handles `}]` appearing inside
    # string values, nested objects, and trailing commentary the non-greedy
    # regex would truncate on. Try each balanced `[...]` until one parses.
    scan_from = 0
    while True:
        span = _extract_first_balanced_array(raw, scan_from)
        if span is None:
            return None
        text, scan_from = span
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            return data


def parse_markdown_findings(raw: str, pass_name: str) -> list[dict]:
    findings: list[dict] = []
    current: dict[str, str] | None = None
    severity_aliases = {"critical", "important", "suggestion", "nit"}
    heading = re.compile(r"^#{1,6}\s+(critical|important|suggestion|nit)\s*:\s*(.+?)\s*$", re.I)
    location = re.compile(r"^(?P<file>[^\s:]+):(?P<line>\d+)\s*[\-\u2013\u2014]\s*(?P<desc>.+)$")
    fix = re.compile(r"^fix\s*:\s*(?P<suggestion>.+)$", re.I)

    def finish() -> None:
        nonlocal current
        if current is not None and current["severity"] in severity_aliases:
            current["pass_name"] = pass_name
            findings.append(current)
        current = None

    for line in raw.splitlines():
        if not line.strip():
            continue
        if m := heading.match(line):
            finish()
            current = {
                "severity": m.group(1).lower(),
                "title": m.group(2).strip(),
                "file": "",
                "line": 0,
                "end_line": 0,
                "description": "",
                "suggestion": "",
            }
            continue
        if current is None:
            continue
        if m := location.match(line):
            current["file"] = m.group("file")
            current["line"] = int(m.group("line"))
            current["end_line"] = int(m.group("line"))
            if not current["description"]:
                current["description"] = m.group("desc").strip()
            continue
        if m := fix.match(line):
            current["suggestion"] = m.group("suggestion").strip()
            continue
        if not current["description"]:
            current["description"] = line.strip()
    finish()

    return findings


def extract_findings(raw: str, pass_name: str) -> list[dict]:
    array = extract_json_array(raw)
    if array is not None:
        for item in array:
            item["pass_name"] = pass_name
        return array
    return parse_markdown_findings(raw, pass_name)


def extract_assistant_text_jsonl(raw: str) -> str:
    assistant_text = ""
    for line in raw.strip().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "message" and event.get("role") == "assistant":
            for block in event.get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    assistant_text = block.get("text", "")
    return assistant_text
