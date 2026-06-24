from __future__ import annotations

import json
import re

from superseded.agents.base import Agent


class CodexAgent(Agent):
    def __init__(self, model: str | None = None) -> None:
        self._model = model or "gpt-4o"

    @property
    def name(self) -> str:
        return "codex"

    def build_command(self, prompt: str) -> list[str]:
        return ["codex", "exec", prompt, "--json", "--model", self._model]

    def parse_output(self, raw: str, pass_name: str) -> list[dict]:
        return _extract_findings_jsonl(raw, pass_name)


def _extract_findings_jsonl(raw: str, pass_name: str) -> list[dict]:
    assistant_text = ""
    for line in raw.strip().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "message" and event.get("role") == "assistant":
            content = event.get("content", [])
            for block in content:
                if block.get("type") == "text":
                    assistant_text = block["text"]
    if not assistant_text:
        return []
    match = re.search(r"\[.*\]", assistant_text, re.DOTALL)
    if not match:
        return []
    try:
        items = json.loads(match.group())
    except json.JSONDecodeError:
        return []
    findings = []
    for item in items:
        item["pass_name"] = pass_name
        findings.append(item)
    return findings
