from __future__ import annotations

from superseded.agents.base import SubprocessAgentAdapter


class OpenCodeAdapter(SubprocessAgentAdapter):
    def __init__(
        self, model: str = "", timeout: int = 600, github_token: str = "", api_key: str = ""
    ) -> None:
        super().__init__(timeout=timeout, github_token=github_token)
        self.model = model
        self._api_key = api_key

    def _build_env(self) -> dict[str, str]:
        env = super()._build_env()
        if self._api_key:
            env["OPENCODE_API_KEY"] = self._api_key
        return env

    def _build_command(self, prompt: str) -> list[str]:
        cmd = ["opencode"]
        if self.model:
            cmd.extend(["-m", self.model])
        cmd.extend(["run", "--pure"])
        cmd.append(prompt)
        return cmd

    def _get_stdin_data(self, prompt: str) -> bytes | None:
        return None
