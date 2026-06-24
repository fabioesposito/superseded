from __future__ import annotations

import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from superseded.agents.base import Agent
from superseded.agents.claude_code import ClaudeCodeAgent
from superseded.agents.codex import CodexAgent
from superseded.agents.opencode import OpenCodeAgent
from superseded.models import Finding, ReviewResult
from superseded.review.merger import merge_findings
from superseded.review.prompts import build_prompt

if TYPE_CHECKING:
    from superseded.config import Config

logger = logging.getLogger(__name__)

AGENT_MAP: dict[str, type[Agent]] = {
    "claude-code": ClaudeCodeAgent,
    "opencode": OpenCodeAgent,
    "codex": CodexAgent,
}


class ReviewEngine:
    def __init__(self, agent: Agent, config: Config) -> None:
        self.agent = agent
        self.config = config

    @classmethod
    def select(cls, agent_name: str, model: str | None) -> ReviewEngine:
        from superseded.config import Config

        agent_cls = AGENT_MAP.get(agent_name)
        if agent_cls is None:
            raise ValueError(f"Unknown agent: {agent_name}. Choose from: {list(AGENT_MAP)}")
        agent = agent_cls(model=model)
        return cls(agent=agent, config=Config())

    def run_pass(self, pass_name: str, prompt: str) -> list[Finding]:
        cmd = self.agent.build_command(prompt)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except FileNotFoundError as err:
            raise RuntimeError(
                f"Agent CLI '{cmd[0]}' not found on PATH. "
                f"Install it or choose a different agent with --agent."
            ) from err
        except subprocess.TimeoutExpired as err:
            raise RuntimeError(f"Agent timed out after 300 seconds for pass: {pass_name}") from err

        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise RuntimeError(
                f"Agent '{cmd[0]}' exited {result.returncode} for pass '{pass_name}'"
                + (f": {stderr}" if stderr else "")
            )

        raw_findings = self.agent.parse_output(result.stdout, pass_name)
        findings = []
        for item in raw_findings:
            try:
                findings.append(Finding(**item))
            except Exception as err:
                logger.warning("Skipping malformed finding item in pass %s: %s", pass_name, err)
        return findings

    def review(
        self,
        diff: str,
        pr_description: str | None = None,
        file_context: str | None = None,
        memory_context: str | None = None,
        static_signals: str | None = None,
        usage_signals: str | None = None,
        passes: list[str] | None = None,
    ) -> ReviewResult:
        if passes is None:
            passes = [
                n
                for n in ["security", "correctness", "performance", "style", "architecture"]
                if self.config.is_pass_enabled(n)
            ]

        all_findings: list[list[Finding]] = []

        with ThreadPoolExecutor(max_workers=max(1, len(passes))) as executor:
            future_to_pass = {}
            for pass_name in passes:
                prompt = build_prompt(
                    pass_name=pass_name,
                    diff=diff,
                    pr_description=pr_description,
                    file_context=file_context,
                    memory_context=memory_context,
                    static_signals=static_signals,
                    usage_signals=usage_signals,
                )
                future = executor.submit(self.run_pass, pass_name, prompt)
                future_to_pass[future] = pass_name

            for future in as_completed(future_to_pass):
                pass_name = future_to_pass[future]
                try:
                    all_findings.append(future.result())
                except Exception as err:
                    logger.warning("Review pass '%s' failed and was skipped: %s", pass_name, err)

        return self.merge_findings(all_findings)

    def merge_findings(self, finding_groups: list[list[Finding]]) -> ReviewResult:
        return merge_findings(finding_groups)
