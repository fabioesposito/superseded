from __future__ import annotations

from superseded.agents import register_agent
from superseded.agents.base import SubprocessAgentAdapter
from superseded.models import AgentContext


@register_agent("codex")
class CodexAdapter(SubprocessAgentAdapter):
    DEFAULT_MODEL = "o4-mini"

    def __init__(
        self, model: str = "", timeout: int = 600, github_token: str = "", api_key: str = ""
    ) -> None:
        super().__init__(timeout=timeout, github_token=github_token)
        self.model = model
        self._api_key = api_key

    def _build_env(self) -> dict[str, str]:
        env = super()._build_env()
        if self._api_key:
            env["OPENAI_API_KEY"] = self._api_key
        return env

    def _build_command(self, prompt: str, context: AgentContext) -> list[str]:
        cmd = ["codex", "--quiet", "--model", self.model or self.DEFAULT_MODEL]
        return cmd

    def _get_stdin_data(self, prompt: str) -> bytes | None:
        return prompt.encode("utf-8")
