from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
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

DEFAULT_PASS_TIMEOUT = 300

ProgressFn = Callable[[str, str], None]

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
    def select(
        cls, agent_name: str, model: str | None, config: Config | None = None
    ) -> ReviewEngine:
        from superseded.config import Config

        agent_cls = AGENT_MAP.get(agent_name)
        if agent_cls is None:
            raise ValueError(f"Unknown agent: {agent_name}. Choose from: {list(AGENT_MAP)}")
        agent = agent_cls(model=model)
        return cls(agent=agent, config=config or Config())

    def run_pass(
        self,
        pass_name: str,
        prompt: str,
        timeout: int = DEFAULT_PASS_TIMEOUT,
        progress: ProgressFn | None = None,
    ) -> list[Finding]:
        cmd = self.agent.build_command()
        if progress is not None:
            progress(pass_name, "start")
        try:
            result = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True, timeout=timeout
            )
        except FileNotFoundError as err:
            raise RuntimeError(
                f"Agent CLI '{cmd[0]}' not found on PATH. "
                f"Install it or choose a different agent with --agent."
            ) from err
        except subprocess.TimeoutExpired as err:
            raise RuntimeError(
                f"Agent timed out after {timeout} seconds for pass: {pass_name}"
            ) from err

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
        if progress is not None:
            progress(pass_name, "done")
        return findings

    def review(
        self,
        diff: str,
        pr_description: str | None = None,
        file_context: str | None = None,
        memory_context: str | None = None,
        static_signals: str | None = None,
        usage_signals: str | None = None,
        conventions_signals: str | None = None,
        spec_signals: str | None = None,
        passes: list[str] | None = None,
        timeout: int = DEFAULT_PASS_TIMEOUT,
        progress: ProgressFn | None = None,
    ) -> ReviewResult:
        if not self.agent.is_available():
            raise RuntimeError(
                f"Agent CLI '{self.agent.name}' not found on PATH. "
                "Install it or choose a different agent with --agent."
            )
        if passes is None or len(passes) == 0:
            passes = [
                n
                for n in ["security", "correctness", "performance", "style", "architecture"]
                if self.config.is_pass_enabled(n)
            ]

        all_findings: list[list[Finding]] = []
        warnings: list[str] = []

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
                    conventions_signals=conventions_signals,
                    spec_signals=spec_signals,
                )
                future = executor.submit(self.run_pass, pass_name, prompt, timeout, progress)
                future_to_pass[future] = pass_name

            for future in as_completed(future_to_pass):
                pass_name = future_to_pass[future]
                try:
                    all_findings.append(future.result())
                except Exception as err:
                    msg = f"Review pass '{pass_name}' failed and was skipped: {err}"
                    logger.warning(msg)
                    warnings.append(msg)
                    if progress is not None:
                        progress(pass_name, "failed")

        result = self.merge_findings(all_findings)
        result.warnings = warnings
        return result

    def merge_findings(self, finding_groups: list[list[Finding]]) -> ReviewResult:
        return merge_findings(finding_groups)
