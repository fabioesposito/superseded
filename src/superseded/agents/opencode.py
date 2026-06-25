from __future__ import annotations

from superseded.agents.base import Agent
from superseded.agents.parsing import extract_findings


class OpenCodeAgent(Agent):
    def __init__(self, model: str | None = None) -> None:
        self._model = model

    @property
    def name(self) -> str:
        return "opencode"

    def build_command(self, prompt: str | None = None) -> list[str]:
        cmd = ["opencode", "run"]
        if prompt is not None:
            cmd.append(prompt)
        if self._model:
            cmd.extend(["--model", self._model])
        return cmd

    def parse_output(self, raw: str, pass_name: str) -> list[dict]:
        return extract_findings(raw, pass_name)
