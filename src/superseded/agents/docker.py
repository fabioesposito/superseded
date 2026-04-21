from __future__ import annotations

from superseded.agents import register_agent
from superseded.agents.base import SubprocessAgentAdapter
from superseded.models import AgentContext


@register_agent("docker")
class DockerAgentAdapter(SubprocessAgentAdapter):
    def __init__(
        self,
        cli: str = "opencode",
        model: str = "",
        timeout: int = 600,
        github_token: str = "",
        api_key: str = "",
    ) -> None:
        if cli not in ["opencode", "claude-code"]:
            raise ValueError(f"Unsupported cli for docker sandbox: {cli}")
        super().__init__(timeout=timeout, github_token=github_token)
        self.cli = cli
        self.model = model
        self._api_key = api_key

    def _build_env(self) -> dict[str, str]:
        env = super()._build_env()
        if self._api_key:
            if self.cli == "claude-code":
                env["ANTHROPIC_API_KEY"] = self._api_key
            elif self.cli == "opencode":
                env["OPENCODE_API_KEY"] = self._api_key
        return env

    def _build_command(self, prompt: str, context: AgentContext) -> list[str]:
        cmd = [
            "docker",
            "run",
            "--rm",
            "-i",
            "-v",
            f"{self._get_cwd(context)}:/workspace",
            "-w",
            "/workspace",
        ]

        import os

        if hasattr(os, "getuid"):
            cmd.extend(["-u", f"{os.getuid()}:{os.getgid()}"])

        env = self._build_env()
        env = {**env, "HOME": env.get("HOME", "/tmp")}
        for k, v in env.items():
            cmd.extend(["-e", f"{k}={v}"])

        if self.cli == "opencode":
            inner_cmd = "pip install --user uv && ~/.local/bin/uvx opencode "
            if self.model:
                inner_cmd += f"-m {self.model} "
            inner_cmd += 'run --pure "$1"'
            cmd.extend(["python:3.12-slim", "sh", "-c", inner_cmd, "--", prompt])
        elif self.cli == "claude-code":
            cmd.extend(
                [
                    "node:20-slim",
                    "npx",
                    "-y",
                    "@anthropic-ai/claude-code",
                    "-p",
                    prompt,
                    "--output-format",
                    "text",
                ]
            )
            if self.model:
                cmd.extend(["--model", self.model])

        return cmd
