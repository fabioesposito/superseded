from __future__ import annotations

from superseded.agents import get_registry
from superseded.agents.base import AgentAdapter


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

    def create(
        self, cli: str | None = None, model: str | None = None, sandbox: str = "host"
    ) -> AgentAdapter:
        cli = cli or self.default_agent
        model = model or self.default_model
        api_key_map = {
            "claude-code": self.anthropic_api_key,
            "opencode": self.opencode_api_key,
            "codex": self.openai_api_key,
        }
        api_key = api_key_map.get(cli, "")

        if sandbox == "docker":
            from superseded.agents.docker import DockerAgentAdapter

            return DockerAgentAdapter(
                cli=cli,
                model=model,
                timeout=self.timeout,
                github_token=self.github_token,
                api_key=api_key,
            )

        registry = get_registry()
        if cli not in registry:
            raise ValueError(f"Unknown agent: {cli}")
        return registry[cli](
            model=model,
            timeout=self.timeout,
            github_token=self.github_token,
            api_key=api_key,
        )
