from __future__ import annotations

from superseded.agents.base import Agent
from superseded.agents.parsing import extract_findings


class ClaudeCodeAgent(Agent):
    def __init__(self, model: str | None = None) -> None:
        self._model = model or "claude-sonnet-4-20250514"

    @property
    def name(self) -> str:
        return "claude-code"

    def build_command(self, prompt: str) -> list[str]:
        return ["claude", "-p", prompt, "--bare", "--model", self._model]

    def parse_output(self, raw: str, pass_name: str) -> list[dict]:
        return extract_findings(raw, pass_name)
