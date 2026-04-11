from __future__ import annotations

from superseded.agents.base import SubprocessAgentAdapter


class ClaudeCodeAdapter(SubprocessAgentAdapter):
    def _build_command(self, prompt: str) -> list[str]:
        return [
            "claude",
            "--print",
            "--output-format",
            "text",
            prompt,
        ]
