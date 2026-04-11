from __future__ import annotations

from superseded.agents.base import SubprocessAgentAdapter


class OpenCodeAdapter(SubprocessAgentAdapter):
    def _build_command(self, prompt: str) -> list[str]:
        return [
            "opencode",
            "--non-interactive",
            prompt,
        ]
