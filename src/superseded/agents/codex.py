from __future__ import annotations

from superseded.agents.base import Agent
from superseded.agents.parsing import extract_assistant_text_jsonl, extract_findings


class CodexAgent(Agent):
    def __init__(self, model: str | None = None) -> None:
        self._model = model or "gpt-5.4-mini"

    @property
    def name(self) -> str:
        return "codex"

    def build_command(self, prompt: str) -> list[str]:
        return ["codex", "exec", prompt, "--json", "--model", self._model]

    def parse_output(self, raw: str, pass_name: str) -> list[dict]:
        text = extract_assistant_text_jsonl(raw)
        if not text:
            return []
        return extract_findings(text, pass_name)
