from __future__ import annotations

from superseded.agents.base import SubprocessAgentAdapter


class ClaudeCodeAdapter(SubprocessAgentAdapter):
    def __init__(
        self, model: str = "", timeout: int = 600, github_token: str = "", api_key: str = ""
    ) -> None:
        super().__init__(timeout=timeout, github_token=github_token)
        self.model = model
        self._api_key = api_key

    def _build_env(self) -> dict[str, str]:
        env = super()._build_env()
        if self._api_key:
            env["ANTHROPIC_API_KEY"] = self._api_key
        return env

    def _build_command(self, prompt: str) -> list[str]:
        cmd = ["claude", "-p", prompt, "--output-format", "text"]
        if self.model:
            cmd.extend(["--model", self.model])
        return cmd

    def _get_stdin_data(self, prompt: str) -> bytes | None:
        return None
