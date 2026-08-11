from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Literal

from superseded.models import Finding, ReviewResult, ReviewUsage
from superseded.providers import PROVIDER_MAP, Provider
from superseded.providers.parsing import parse_findings_json
from superseded.review.merger import merge_findings
from superseded.review.prompts import build_prompt, build_retry_prompt
from superseded.review.verifier import _parse_verdicts

if TYPE_CHECKING:
    from superseded.config import Config

logger = logging.getLogger(__name__)

DEFAULT_PASS_TIMEOUT = 600

ProgressFn = Callable[[str, str], None]


class ReviewEngine:
    def __init__(self, provider: Provider, config: Config) -> None:
        self.provider = provider
        self.model: str | None = None
        self.reasoning_effort: Literal["low", "medium", "high", "max"] = config.reasoning_effort
        self.config = config

    @classmethod
    def select(
        cls, provider_name: str, model: str | None, config: Config | None = None
    ) -> ReviewEngine:
        from superseded.config import Config

        provider_cls = PROVIDER_MAP.get(provider_name)
        if provider_cls is None:
            raise ValueError(
                f"Unknown provider: {provider_name}. Choose from: {list(PROVIDER_MAP)}"
            )
        provider = provider_cls()
        engine = cls(provider=provider, config=config or Config())
        engine.model = model
        return engine

    def run_pass(
        self,
        pass_name: str,
        prompt: str,
        timeout: int = DEFAULT_PASS_TIMEOUT,
        progress: ProgressFn | None = None,
    ) -> tuple[list[Finding], ReviewUsage]:
        if progress is not None:
            progress(pass_name, "start")
        findings, errors, usage = self._run_and_validate(pass_name, prompt, timeout)
        if errors:
            logger.info("Retrying pass %s: %d finding(s) failed validation", pass_name, len(errors))
            retried, _, retry_usage = self._run_and_validate(
                pass_name, build_retry_prompt(prompt, errors), timeout
            )
            if retried:
                findings = retried
                # The retry supersedes the first attempt's findings; credit its
                # usage alone rather than accumulating both calls.
                usage = retry_usage
        if progress is not None:
            progress(pass_name, "done")
        return findings, usage

    def _run_and_validate(
        self, pass_name: str, prompt: str, timeout: int
    ) -> tuple[list[Finding], list[str], ReviewUsage]:
        resp = self.provider.complete(
            prompt, model=self.model, timeout=timeout, reasoning_effort=self.reasoning_effort
        )
        raw_findings = parse_findings_json(resp.content, pass_name)
        findings: list[Finding] = []
        errors: list[str] = []
        for item in raw_findings:
            try:
                findings.append(Finding(**item))
            except Exception as err:
                errors.append(str(err))
                logger.warning("Skipping malformed finding item in pass %s: %s", pass_name, err)
        usage = ReviewUsage(
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            per_pass={pass_name: (resp.prompt_tokens, resp.completion_tokens)},
        )
        return findings, errors, usage

    def _run_verification(
        self,
        result: ReviewResult,
        diff: str,
        file_context: str | None,
        timeout: int,
    ) -> ReviewResult:
        """Run a post-merge verification pass over the deduplicated findings."""
        from superseded.review.prompts import build_verify_prompt

        if not result.findings:
            return result

        prompt = build_verify_prompt(result.findings, diff, file_context)
        try:
            resp = self.provider.complete(
                prompt, model=self.model, timeout=timeout, reasoning_effort=self.reasoning_effort
            )
        except Exception as err:
            logger.warning("Verification pass failed: %s", err)
            result.warnings.append(f"Verification pass failed: {err}")
            return result

        errors, verdicts = _parse_verdicts(resp.content, collect_errors=True)

        kept: list[Finding] = []
        dropped_findings: list[Finding] = []
        dropped_count = 0
        reestimated_count = 0

        for f in result.findings:
            verdict = verdicts.get(f.id)
            if verdict is None:
                f.verification = "kept"
                kept.append(f)
                continue
            if verdict.action == "drop":
                f.verification = "dropped"
                f.verification_reason = verdict.reason
                dropped_count += 1
                dropped_findings.append(f)
                continue
            f.verification = "kept"
            if verdict.severity is not None:
                f.severity = verdict.severity
                f.verified_severity = verdict.severity
                reestimated_count += 1
            if verdict.confidence is not None:
                f.confidence = verdict.confidence
            if verdict.reason:
                f.verification_reason = verdict.reason
            kept.append(f)

        for err in errors:
            logger.warning("Verification parse error: %s", err)

        dropped_msg = f"Verification completed: {dropped_count} findings dropped, {len(kept)} kept"
        if reestimated_count:
            dropped_msg += f" ({reestimated_count} re-estimated)"
        result.warnings.append(dropped_msg)

        # Accumulate verify-pass tokens into result.usage too.
        result.usage.prompt_tokens += resp.prompt_tokens
        result.usage.completion_tokens += resp.completion_tokens
        result.usage.per_pass["verify"] = (resp.prompt_tokens, resp.completion_tokens)

        return ReviewResult(
            findings=kept,
            warnings=result.warnings,
            dropped_findings=dropped_findings,
            usage=result.usage,
        )

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
        learned_context: str | None = None,
        passes: list[str] | None = None,
        timeout: int = DEFAULT_PASS_TIMEOUT,
        progress: ProgressFn | None = None,
    ) -> ReviewResult:
        if passes is None or len(passes) == 0:
            passes = [
                n
                for n in ["security", "correctness", "performance", "style", "architecture"]
                if self.config.is_pass_enabled(n)
            ]

        all_findings: list[list[Finding]] = []
        warnings: list[str] = []
        total_usage = ReviewUsage()

        with ThreadPoolExecutor(max_workers=max(1, len(passes))) as pool:
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
                    learned_context=learned_context,
                )
                future = pool.submit(self.run_pass, pass_name, prompt, timeout, progress)
                future_to_pass[future] = pass_name

            for future in as_completed(future_to_pass):
                pass_name = future_to_pass[future]
                try:
                    findings, usage = future.result()
                    all_findings.append(findings)
                    total_usage.prompt_tokens += usage.prompt_tokens
                    total_usage.completion_tokens += usage.completion_tokens
                    total_usage.per_pass.update(usage.per_pass)
                except Exception as err:
                    msg = f"Review pass '{pass_name}' failed and was skipped: {err}"
                    logger.warning(msg)
                    warnings.append(msg)
                    if progress is not None:
                        progress(pass_name, "failed")

            result = self.merge_findings(all_findings)
            result.warnings = warnings
            result.usage = total_usage

            if self.config.verify and result.findings:
                result = self._run_verification(result, diff, file_context, timeout)

        return result

    def merge_findings(self, finding_groups: list[list[Finding]]) -> ReviewResult:
        return merge_findings(finding_groups)
