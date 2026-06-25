from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from superseded.models import ReviewResult
    from superseded.server.github import GitHubApp
    from superseded.server.repo_manager import RepoManager

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = ("critical", "important", "suggestion", "nit")
DISK_USAGE_LIMIT = 0.9


@dataclass
class ReviewJob:
    installation_id: int
    owner: str
    repo: str
    pr_number: int
    head_sha: str
    base_sha: str


@dataclass
class ReviewOutcome:
    conclusion: str
    title: str
    summary: str


def build_check_run_title(result: ReviewResult) -> str:
    total = len(result.findings)
    summary = result.summary
    parts = [f"{summary[sev]} {sev}" for sev in _SEVERITY_ORDER if summary.get(sev, 0)]
    return f"{total} findings ({', '.join(parts)})" if parts else f"{total} findings"


class ReviewWorker:
    def __init__(
        self,
        github: GitHubApp,
        repo_manager: RepoManager,
        max_concurrent: int = 3,
    ) -> None:
        self.github = github
        self.repo_manager = repo_manager
        self.queue: asyncio.Queue[ReviewJob] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active_count = 0

    @property
    def active_count(self) -> int:
        return self._active_count

    async def enqueue(self, job: ReviewJob) -> None:
        await self.queue.put(job)

    async def run(self) -> None:
        while True:
            job = await self.queue.get()
            try:
                await self._process(job)
            finally:
                self.queue.task_done()

    async def _process(self, job: ReviewJob) -> None:
        correlation_id = str(uuid.uuid4())[:8]
        logger.info(
            "review_started",
            extra={
                "correlation_id": correlation_id,
                "repo": f"{job.owner}/{job.repo}",
                "pr": job.pr_number,
            },
        )

        try:
            token = await self.github.get_installation_token(job.installation_id)
        except Exception:
            logger.exception(
                "review_failed",
                extra={
                    "correlation_id": correlation_id,
                    "repo": f"{job.owner}/{job.repo}",
                    "pr": job.pr_number,
                },
            )
            return

        check_run_id = None
        async with self._semaphore:
            self._active_count += 1
            try:
                check_run_id = await self.github.create_check_run(
                    token=token,
                    owner=job.owner,
                    repo=job.repo,
                    name="Superseded Review",
                    head_sha=job.head_sha,
                    status="in_progress",
                )

                outcome = await _run_review_for_job(
                    github=self.github,
                    repo_manager=self.repo_manager,
                    token=token,
                    job=job,
                    correlation_id=correlation_id,
                )

                await self.github.update_check_run(
                    token=token,
                    owner=job.owner,
                    repo=job.repo,
                    check_run_id=check_run_id,
                    status="completed",
                    conclusion=outcome.conclusion,
                    title=outcome.title,
                    summary=outcome.summary,
                )
            except Exception:
                logger.exception(
                    "review_failed",
                    extra={
                        "correlation_id": correlation_id,
                        "repo": f"{job.owner}/{job.repo}",
                        "pr": job.pr_number,
                    },
                )
                if check_run_id is not None:
                    try:
                        await self.github.update_check_run(
                            token=token,
                            owner=job.owner,
                            repo=job.repo,
                            check_run_id=check_run_id,
                            status="completed",
                            conclusion="failure",
                            title="Review failed",
                            summary=f"Review failed. Correlation ID: {correlation_id}",
                        )
                    except Exception:
                        logger.exception("Failed to update check run on error")
            finally:
                self._active_count -= 1


async def _run_review_for_job(
    github: GitHubApp,
    repo_manager: RepoManager,
    token: str,
    job: ReviewJob,
    correlation_id: str,
) -> None:
    from superseded.config import load_config
    from superseded.review.engine import ReviewEngine

    tmp_dir = repo_manager.job_dir(job.installation_id, job.owner, job.repo, job.pr_number)

    try:
        from superseded.server.checkout import checkout_repo

        if repo_manager.disk_usage() > DISK_USAGE_LIMIT:
            raise RuntimeError(
                f"Disk usage above {DISK_USAGE_LIMIT:.0%} limit "
                f"({repo_manager.disk_usage():.0%}); skipping clone to avoid filling temp dir."
            )

        repo_path = await checkout_repo(
            token=token,
            owner=job.owner,
            repo=job.repo,
            ref=job.head_sha,
            tmp_dir=str(tmp_dir),
        )

        config = load_config(repo_path / ".superseded.yaml")

        diff = await github.fetch_pr_diff(token, job.owner, job.repo, job.pr_number)
        pr_description = await github.fetch_pr_description(
            token, job.owner, job.repo, job.pr_number
        )

        from concurrent.futures import ThreadPoolExecutor

        from superseded.context.conventions import discover_conventions
        from superseded.context.spec_retrieval import discover_repo_specs
        from superseded.context.static_analysis import run_static_analysis
        from superseded.context.usage_retrieval import retrieve_usages
        from superseded.diff import compute_file_context, parse_diff_files

        changed_files = (
            [e["file"] for e in parse_diff_files(diff)]
            if (config.static_analysis or config.usage_retrieval)
            else []
        )

        with ThreadPoolExecutor(max_workers=4) as ctx_executor:
            ctx_futures = {
                "file_context": ctx_executor.submit(compute_file_context, diff, root=repo_path),
                "static_signals": ctx_executor.submit(run_static_analysis, changed_files, repo_path)
                if config.static_analysis
                else None,
                "usage_signals": ctx_executor.submit(retrieve_usages, diff, repo_path)
                if config.usage_retrieval
                else None,
                "conventions_signals": ctx_executor.submit(discover_conventions, repo_path)
                if config.conventions
                else None,
                "spec_signals": ctx_executor.submit(discover_repo_specs, diff, repo_path)
                if config.spec_retrieval
                else None,
            }

            def _result(key: str) -> str | None:
                f = ctx_futures.get(key)
                if f is None:
                    return None
                val = f.result()
                return val or None

            file_context = _result("file_context")
            static_signals = _result("static_signals")
            usage_signals = _result("usage_signals")
            conventions_signals = _result("conventions_signals")
            spec_signals = _result("spec_signals")

        engine = ReviewEngine.select(config.agent, model=config.model, config=config)
        result = engine.review(
            diff=diff,
            pr_description=pr_description,
            file_context=file_context,
            static_signals=static_signals,
            usage_signals=usage_signals,
            conventions_signals=conventions_signals,
            spec_signals=spec_signals,
        )

        from superseded.output.github_pr import build_review_payload

        payload = build_review_payload(result)

        await github.post_review(
            token=token,
            owner=job.owner,
            repo=job.repo,
            pr_number=job.pr_number,
            body=payload["body"],
            comments=payload["comments"],
            event=payload["event"],
        )

        conclusion = "success" if payload["event"] != "REQUEST_CHANGES" else "failure"
        title = build_check_run_title(result)
        passes_used = sorted({f.pass_name for f in result.findings})
        summary = (
            f"Review completed. {len(result.findings)} findings across {len(passes_used)} pass(es)."
        )

        logger.info(
            "review_completed",
            extra={
                "correlation_id": correlation_id,
                "repo": f"{job.owner}/{job.repo}",
                "pr": job.pr_number,
                "findings_count": len(result.findings),
            },
        )
        return ReviewOutcome(conclusion=conclusion, title=title, summary=summary)
    finally:
        repo_manager.cleanup(tmp_dir)
