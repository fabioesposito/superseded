from __future__ import annotations

from superseded.agents.base import SubprocessAgentAdapter


class OpenCodeAdapter(SubprocessAgentAdapter):
    def __init__(self, model: str = "", timeout: int = 600) -> None:
        super().__init__(timeout=timeout)
        self.model = model

    def _build_command(self, prompt: str) -> list[str]:
        cmd = ["opencode", "--non-interactive"]
        if self.model:
            cmd.extend(["--model", self.model])
        return cmd

    def _get_stdin_data(self, prompt: str) -> bytes | None:
        return prompt.encode("utf-8")
