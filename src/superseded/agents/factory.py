from __future__ import annotations

from superseded.agents.base import AgentAdapter
from superseded.agents.claude_code import ClaudeCodeAdapter
from superseded.agents.codex import CodexAdapter
from superseded.agents.opencode import OpenCodeAdapter


class AgentFactory:
    def __init__(
        self,
        default_agent: str = "claude-code",
        default_model: str = "",
        timeout: int = 600,
        github_token: str = "",
        openai_api_key: str = "",
        anthropic_api_key: str = "",
        opencode_api_key: str = "",
    ) -> None:
        self.default_agent = default_agent
        self.default_model = default_model
        self.timeout = timeout
        self.github_token = github_token
        self.openai_api_key = openai_api_key
        self.anthropic_api_key = anthropic_api_key
        self.opencode_api_key = opencode_api_key

    def create(self, cli: str | None = None, model: str | None = None) -> AgentAdapter:
        cli = cli or self.default_agent
        model = model or self.default_model
        if cli == "claude-code":
            return ClaudeCodeAdapter(
                model=model,
                timeout=self.timeout,
                github_token=self.github_token,
                api_key=self.anthropic_api_key,
            )
        elif cli == "opencode":
            return OpenCodeAdapter(
                model=model,
                timeout=self.timeout,
                github_token=self.github_token,
                api_key=self.opencode_api_key,
            )
        elif cli == "codex":
            return CodexAdapter(
                model=model,
                timeout=self.timeout,
                github_token=self.github_token,
                api_key=self.openai_api_key,
            )
        raise ValueError(f"Unknown agent CLI: {cli}")
