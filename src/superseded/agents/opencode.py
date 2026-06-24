from __future__ import annotations

import json
import re

from superseded.agents.base import Agent


class OpenCodeAgent(Agent):
    def __init__(self, model: str | None = None) -> None:
        self._model = model

    @property
    def name(self) -> str:
        return "opencode"

    def build_command(self, prompt: str) -> list[str]:
        cmd = ["opencode", "run", prompt]
        return cmd

    def parse_output(self, raw: str, pass_name: str) -> list[dict]:
        return _extract_findings(raw, pass_name)


def _extract_findings(raw: str, pass_name: str) -> list[dict]:
    match = re.search(r"\[.*\]", raw, re.DOTALL)
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
