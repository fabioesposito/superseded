from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import yaml

from superseded.config import Config
from superseded.context.gathering import gather_context
from superseded.models import ReviewResult
from superseded.output.github_pr import build_review_payload
from superseded.review.engine import ReviewEngine
from superseded.server.checkout import checkout_repo

if TYPE_CHECKING:
    from superseded.memory.store import MemoryStore
    from superseded.server.github import GitHubApp
    from superseded.server.repo_manager import RepoManager

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = ("critical", "important", "suggestion", "nit")
DISK_USAGE_LIMIT = 0.9
MAX_DIFF_CHARS = 1_000_000


@dataclass
class ReviewJob:
    installation_id: int
    owner: str
    repo: str
    pr_number: int
    head_sha: str
    base_sha: str
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


@dataclass
class ReviewOutcome:
    conclusion: str
    title: str
    summary: str = ""


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
        max_queue: int = 100,
        store: MemoryStore | None = None,
    ) -> None:
        self.github = github
        self.repo_manager = repo_manager
        # Bounded pending queue: a webhook flood is rejected (callers drop /
        # 429) rather than allowed to grow without limit and exhaust memory.
        self.queue: asyncio.Queue[ReviewJob] = asyncio.Queue(maxsize=max_queue)
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._lock = asyncio.Lock()
        self._active_count = 0
        self._tasks: set[asyncio.Task] = set()
        self.store = store

    @property
    def active_count(self) -> int:
        return self._active_count

    async def enqueue(self, job: ReviewJob) -> None:
        # put_nowait raises QueueFull instantly; a failed enqueue is logged and
        # surfaced to the caller (webhook handler turns it into a 429) rather
        # than blocking the handler indefinitely or growing without limit.
        try:
            self.queue.put_nowait(job)
        except asyncio.QueueFull:
            logger.warning(
                "review_queue_full",
                extra={
                    "repo": f"{job.owner}/{job.repo}",
                    "pr": job.pr_number,
                    "queue_size": self.queue.maxsize,
                },
            )
            raise

    async def run(self) -> None:
        """Consumer loop: spawn a bounded task for each queued job."""
        while True:
            job = await self.queue.get()
            task = asyncio.create_task(self._run_task(job))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            task.add_done_callback(lambda _: self.queue.task_done())

    async def _run_task(self, job: ReviewJob) -> None:
        """Acquire semaphore, process job."""
        try:
            async with self._semaphore:
                async with self._lock:
                    self._active_count += 1
                try:
                    await self._process(job)
                finally:
                    async with self._lock:
                        self._active_count -= 1
        except asyncio.CancelledError:
            logger.info(
                "review_cancelled",
                extra={"repo": f"{job.owner}/{job.repo}", "pr": job.pr_number},
            )

    async def shutdown(self, timeout: float = 10.0) -> None:
        """Drain the queue and cancel any in-flight tasks."""
        try:
            await asyncio.wait_for(self.queue.join(), timeout=timeout)
        except TimeoutError:
            logger.warning(
                "Shutdown timeout reached with %d job(s) still in queue",
                self.queue.qsize(),
            )
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

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
                store=self.store,
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
        except asyncio.CancelledError:
            if check_run_id is not None:
                try:
                    await self.github.update_check_run(
                        token=token,
                        owner=job.owner,
                        repo=job.repo,
                        check_run_id=check_run_id,
                        status="completed",
                        conclusion="failure",
                        title="Review cancelled",
                        summary=(f"Review cancelled (shutdown). Correlation ID: {correlation_id}"),
                    )
                except Exception:
                    logger.exception("Failed to update check run on cancellation")
            raise
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


async def _load_safe_config(github: GitHubApp, token: str, owner: str, repo: str) -> Config:
    """Load repo config from the default branch (trusted), not the PR head.

    A PR can commit a malicious ``.superseded.yaml`` that disables
    ``static_analysis`` (suppressing gitleaks) or forces an expensive
    ``agent``/``model``. Reading from the default branch avoids this.
    ``static_analysis`` is forced on regardless, so secret scanning
    cannot be suppressed by repo config in server mode.
    """
    try:
        raw = await github.fetch_repo_file(token, owner, repo, ".superseded.yaml")
    except Exception:
        logger.warning("Failed to fetch .superseded.yaml from default branch, using defaults")
        raw = None
    if raw:
        try:
            data = yaml.safe_load(raw) or {}
        except yaml.YAMLError:
            logger.warning("Malformed .superseded.yaml on default branch, using defaults")
            data = {}
        config = Config(**data)
    else:
        config = Config()
    config.static_analysis = True
    return config


async def _run_review_for_job(
    github: GitHubApp,
    repo_manager: RepoManager,
    token: str,
    job: ReviewJob,
    correlation_id: str,
    store: MemoryStore | None = None,
) -> ReviewOutcome:
    tmp_dir = repo_manager.job_dir(
        job.installation_id, job.owner, job.repo, job.pr_number, job.job_id
    )

    try:
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

        config = await _load_safe_config(github, token, job.owner, job.repo)

        diff = await github.fetch_pr_diff(token, job.owner, job.repo, job.pr_number)
        if len(diff) > MAX_DIFF_CHARS:
            diff = diff[:MAX_DIFF_CHARS] + (f"\n\n... (diff truncated at {MAX_DIFF_CHARS:,} chars)")
        pr_description = await github.fetch_pr_description(
            token, job.owner, job.repo, job.pr_number
        )

        context = await asyncio.to_thread(
            gather_context,
            diff,
            repo_path,
            static_analysis=config.static_analysis,
            usage_retrieval=config.usage_retrieval,
            conventions=config.conventions,
            spec_retrieval=config.spec_retrieval,
        )
        file_context = context["file_context"]
        static_signals = context["static_signals"]
        usage_signals = context["usage_signals"]
        conventions_signals = context["conventions_signals"]
        spec_signals = context["spec_signals"]

        engine = ReviewEngine.select(config.agent, model=config.model, config=config)
        result = await asyncio.to_thread(
            engine.review,
            diff=diff,
            pr_description=pr_description,
            file_context=file_context,
            static_signals=static_signals,
            usage_signals=usage_signals,
            conventions_signals=conventions_signals,
            spec_signals=spec_signals,
            cwd=repo_path,
        )

        payload = build_review_payload(result)

        comment_ids = await github.post_review(
            token=token,
            owner=job.owner,
            repo=job.repo,
            pr_number=job.pr_number,
            body=payload["body"],
            comments=payload["comments"],
            event=payload["event"],
        )

        if store is not None:
            repo_key = f"{job.owner}/{job.repo}"
            async with store:
                for f in result.findings:
                    await store.record_finding(
                        finding_id=f.id,
                        repo=repo_key,
                        pass_name=f.pass_name,
                        severity=f.severity,
                        file=f.file,
                        line=f.line,
                        title=f.title,
                        description=f.description,
                        reasoning=f.reasoning,
                    )
                for finding, cid in zip(result.findings, comment_ids, strict=True):
                    if cid is not None:
                        await store.set_comment_id(finding.id, cid)

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
