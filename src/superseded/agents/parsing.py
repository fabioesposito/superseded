from __future__ import annotations

import json
import re

JSON_ARRAY_RE = re.compile(r"\[\s*\{.*?\}\s*\]", re.DOTALL)


def extract_json_array(raw: str) -> list[dict] | None:
    match = JSON_ARRAY_RE.search(raw)
    if not match:
        return None
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
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
