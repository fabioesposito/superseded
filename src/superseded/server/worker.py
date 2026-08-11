from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import yaml

from superseded.audit.guidelines import assemble_learned_context, format_memory_context
from superseded.audit.stats import StatsAggregator
from superseded.config import Config
from superseded.context.gathering import gather_context
from superseded.models import ReviewResult
from superseded.output.github_pr import build_review_payload
from superseded.providers import Provider
from superseded.review.engine import ReviewEngine
from superseded.server.checkout import checkout_repo

if TYPE_CHECKING:
    from superseded.memory.backend import Store
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
    passes: list[str] | None = None
    post: bool = True


@dataclass
class ReviewOutcome:
    conclusion: str
    title: str
    summary: str = ""


@dataclass
class JobStatus:
    job_id: str
    status: str  # "queued" | "running" | "completed" | "failed"
    result: ReviewResult | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None


JOB_REGISTRY_CAP = 1000


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
        store: Store | None = None,
        server_provider: str | None = None,
        server_model: str | None = None,
        server_reasoning_effort: str | None = None,
        provider: Provider | None = None,
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
        self.server_provider = server_provider
        self.server_model = server_model
        self.server_reasoning_effort = server_reasoning_effort
        if provider is None:
            raise ValueError("ReviewWorker requires a Provider")
        self._provider = provider
        self._jobs: dict[str, JobStatus] = {}
        self._job_cap = JOB_REGISTRY_CAP

    @property
    def active_count(self) -> int:
        return self._active_count

    def _record_job(
        self,
        job_id: str,
        status: str,
        result: ReviewResult | None = None,
        error: str | None = None,
    ) -> None:
        """Insert or update a job's status; evict oldest when over cap.

        Single-threaded: called only from the worker event loop (enqueue,
        _run_task, _process). No lock needed within one asyncio loop.
        """
        existing = self._jobs.get(job_id)
        created_at = existing.created_at if existing else time.time()
        completed_at = (
            time.time()
            if status in ("completed", "failed")
            else (existing.completed_at if existing else None)
        )
        self._jobs[job_id] = JobStatus(
            job_id=job_id,
            status=status,
            result=result if result is not None else (existing.result if existing else None),
            error=error if error is not None else (existing.error if existing else None),
            created_at=created_at,
            completed_at=completed_at,
        )
        if len(self._jobs) > self._job_cap:
            for stale_id in sorted(self._jobs, key=lambda j: self._jobs[j].created_at)[
                : len(self._jobs) - self._job_cap
            ]:
                self._jobs.pop(stale_id, None)

    def get_job_status(self, job_id: str) -> JobStatus | None:
        return self._jobs.get(job_id)

    async def enqueue(self, job: ReviewJob) -> None:
        self._record_job(job.job_id, "queued")
        # put_nowait raises QueueFull instantly; a failed enqueue is logged and
        # surfaced to the caller (webhook handler turns it into a 429) rather
        # than blocking the handler indefinitely or growing without limit.
        try:
            self.queue.put_nowait(job)
        except asyncio.QueueFull:
            self._record_job(job.job_id, "failed", error="queue full")
            logger.warning(
                "review_queue_full",
                extra={
                    "repo": f"{job.owner}/{job.repo}",
                    "pr": job.pr_number,
                    "queue_size": self.queue.maxsize,
                },
            )
            raise

    def _log_task_done(self, task: asyncio.Task) -> None:
        try:
            _ = task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("worker_task_unhandled_error")

    async def run(self) -> None:
        """Consumer loop: spawn a bounded task for each queued job."""
        while True:
            job = await self.queue.get()
            task = asyncio.create_task(self._run_task(job))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            task.add_done_callback(self._log_task_done)
            task.add_done_callback(lambda _: self.queue.task_done())

    async def _run_task(self, job: ReviewJob) -> None:
        """Acquire semaphore, process job."""
        try:
            async with self._semaphore:
                async with self._lock:
                    self._active_count += 1
                self._record_job(job.job_id, "running")
                try:
                    await self._process(job)
                finally:
                    async with self._lock:
                        self._active_count -= 1
        except asyncio.CancelledError:
            self._record_job(job.job_id, "failed", error="cancelled")
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
        except Exception as err:
            logger.exception(
                "review_failed",
                extra={
                    "correlation_id": correlation_id,
                    "repo": f"{job.owner}/{job.repo}",
                    "pr": job.pr_number,
                },
            )
            self._record_job(job.job_id, "failed", error=f"token fetch failed: {err}")
            return

        check_run_id = None
        try:
            if job.post:
                check_run_id = await self.github.create_check_run(
                    token=token,
                    owner=job.owner,
                    repo=job.repo,
                    name="Superseded Review",
                    head_sha=job.head_sha,
                    status="in_progress",
                )

            outcome, result = await _run_review_for_job(
                github=self.github,
                repo_manager=self.repo_manager,
                token=token,
                job=job,
                correlation_id=correlation_id,
                store=self.store,
                server_provider=self.server_provider,
                server_model=self.server_model,
                server_reasoning_effort=self.server_reasoning_effort,
                provider=self._provider,
            )

            if check_run_id is not None:
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
            self._record_job(job.job_id, "completed", result=result)
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
            self._record_job(job.job_id, "failed", error="cancelled")
            raise
        except Exception as err:
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
            self._record_job(job.job_id, "failed", error=str(err))


async def _load_safe_config(
    github: GitHubApp,
    token: str,
    owner: str,
    repo: str,
    installation_id: int | None = None,
    store: Store | None = None,
    server_provider: str | None = None,
    server_model: str | None = None,
    server_reasoning_effort: str | None = None,
) -> Config:
    """Load repo config from the default branch (trusted), not the PR head.

    A PR can commit a malicious ``.superseded.yaml`` that disables
    ``static_analysis`` (suppressing gitleaks) or forces an expensive
    ``provider``/``model``. Reading from the default branch avoids this.
    ``static_analysis`` is forced on regardless, so secret scanning
    cannot be suppressed by repo config in server mode.  The server
    operator can also pin a specific provider/model/reasoning effort,
    overriding any repo-level choice.

    Per-installation config overrides from ``installation_config`` take
    precedence over the repo's ``.superseded.yaml`` but are overridden by
    server-level settings.
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

    if installation_id is not None and store is not None:
        try:
            overrides = await store.get_installation_config(installation_id)
            for key, value in overrides.items():
                if hasattr(config, key):
                    try:
                        setattr(config, key, type(getattr(config, key))(value))
                    except ValueError, TypeError:
                        setattr(config, key, value)
        except Exception:
            logger.warning("Failed to load installation config overrides for %d", installation_id)

    config.static_analysis = True
    config.passes.security = True
    if server_provider is not None:
        config.provider = server_provider
    if server_model is not None:
        config.model = server_model
    if server_reasoning_effort is not None:
        config.reasoning_effort = server_reasoning_effort
    return config


async def _run_review_for_job(
    github: GitHubApp,
    repo_manager: RepoManager,
    token: str,
    job: ReviewJob,
    correlation_id: str,
    store: Store | None = None,
    server_provider: str | None = None,
    server_model: str | None = None,
    server_reasoning_effort: str | None = None,
    provider: Provider | None = None,
) -> tuple[ReviewOutcome, ReviewResult]:
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

        config = await _load_safe_config(
            github,
            token,
            job.owner,
            job.repo,
            installation_id=job.installation_id,
            store=store,
            server_provider=server_provider,
            server_model=server_model,
            server_reasoning_effort=server_reasoning_effort,
        )

        repo_key = f"{job.owner}/{job.repo}"
        incremental: str | None = None
        if config.progressive and store is not None:
            watermark = await store.get_watermark(repo_key, job.pr_number)
            if watermark is not None:
                if watermark == job.head_sha:
                    logger.info(
                        "review_skipped_noop",
                        extra={
                            "correlation_id": correlation_id,
                            "repo": repo_key,
                            "pr": job.pr_number,
                        },
                    )
                    return (
                        ReviewOutcome(
                            conclusion="success",
                            title="No new commits since last review",
                            summary=f"Head {job.head_sha[:7]} unchanged since last review.",
                        ),
                        ReviewResult(),
                    )
                try:
                    patch, status = await github.compare_diff(
                        token, job.owner, job.repo, watermark, job.head_sha
                    )
                except Exception:
                    logger.warning(
                        "compare_failed",
                        extra={
                            "correlation_id": correlation_id,
                            "repo": repo_key,
                            "pr": job.pr_number,
                        },
                    )
                    patch, status = None, "diverged"
                if status == "ahead" and patch is not None:
                    incremental = patch
                    logger.info(
                        "review_progressive",
                        extra={
                            "correlation_id": correlation_id,
                            "mode": "incremental",
                            "base_sha": watermark,
                            "head_sha": job.head_sha,
                        },
                    )

        if incremental is not None:
            diff = incremental
        else:
            diff = await github.fetch_pr_diff(token, job.owner, job.repo, job.pr_number)
            logger.info(
                "review_progressive",
                extra={
                    "correlation_id": correlation_id,
                    "mode": "full",
                    "head_sha": job.head_sha,
                },
            )
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
            graph=config.graph,
        )
        file_context = context["file_context"]
        static_signals = context["static_signals"]
        usage_signals = context["usage_signals"]
        conventions_signals = context["conventions_signals"]
        spec_signals = context["spec_signals"]

        memory_context: str | None = None
        if store is not None:
            dismissed = await store.get_dismissed_findings(repo_key)
            memory_context = format_memory_context(dismissed)

        learned_context: str | None = None
        if config.learned_review and store is not None:
            await store.prune_stale_rules(repo_key)
            all_rules = await store.get_learned_rules(repo_key, limit=config.max_learned_rules)
            learned_context = assemble_learned_context(None, all_rules, config.max_learned_rules)

        engine = ReviewEngine(provider=provider, config=config)
        engine.model = config.model
        engine.reasoning_effort = config.reasoning_effort
        result = await asyncio.to_thread(
            engine.review,
            diff=diff,
            pr_description=pr_description,
            file_context=file_context,
            memory_context=memory_context,
            static_signals=static_signals,
            usage_signals=usage_signals,
            conventions_signals=conventions_signals,
            spec_signals=spec_signals,
            learned_context=learned_context,
            passes=job.passes,
            timeout=600,
            progress=None,
        )

        comment_ids: list[int | None] = []
        event = "COMMENT"
        if job.post:
            payload = build_review_payload(result)
            event = payload["event"]
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
                if result.findings:
                    await store.record_findings_batch(
                        [
                            {
                                "id": f.id,
                                "pass_name": f.pass_name,
                                "severity": f.severity,
                                "file": f.file,
                                "line": f.line,
                                "title": f.title,
                                "description": f.description,
                                "reasoning": f.reasoning,
                            }
                            for f in result.findings
                        ],
                        repo_key,
                    )
                if job.post:
                    pairs = [
                        (f.id, cid)
                        for f, cid in zip(result.findings, comment_ids, strict=True)
                        if cid is not None
                    ]
                    if pairs:
                        await store.set_comment_ids_batch(pairs)
                await store.set_watermark(repo_key, job.pr_number, job.head_sha)

            if config.learned_review:
                from superseded.audit.reflector import PatternReflector

                aggregator = StatsAggregator(store)
                await aggregator._refresh(repo_key)
                stats_text = await aggregator.get_stats_context(repo_key)

                reflector = PatternReflector(
                    provider=provider, store=store, threshold=config.reflection_threshold
                )
                await reflector.maybe_reflect(repo_key)

                await store.prune_stale_rules(repo_key)
                all_rules = await store.get_learned_rules(repo_key, limit=config.max_learned_rules)
                learned_context = assemble_learned_context(
                    stats_text, all_rules, config.max_learned_rules
                )

        conclusion = "success" if event != "REQUEST_CHANGES" else "failure"
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
        return ReviewOutcome(conclusion=conclusion, title=title, summary=summary), result
    finally:
        repo_manager.cleanup(tmp_dir)
