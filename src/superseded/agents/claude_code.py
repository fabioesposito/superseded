from __future__ import annotations

import os

from superseded.agents.base import Agent
from superseded.agents.parsing import extract_findings


class ClaudeCodeAgent(Agent):
    def __init__(self, model: str | None = None) -> None:
        self._model = model or "claude-sonnet-4-6"

    @property
    def name(self) -> str:
        return "claude-code"

    def build_command(self) -> list[str]:
        cmd = ["claude", "-p", "-"]
        if os.environ.get("ANTHROPIC_API_KEY"):
            cmd.append("--bare")
        cmd.extend(["--model", self._model])
        return cmd

    def parse_output(self, raw: str, pass_name: str) -> list[dict]:
        return extract_findings(raw, pass_name)
