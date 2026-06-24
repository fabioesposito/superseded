from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from superseded.server.github import GitHubApp
    from superseded.server.repo_manager import RepoManager

logger = logging.getLogger(__name__)


@dataclass
class ReviewJob:
    installation_id: int
    owner: str
    repo: str
    pr_number: int
    head_sha: str
    base_sha: str


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

        check_run_id = None
        async with self._semaphore:
            self._active_count += 1
            try:
                token = await self.github.get_installation_token(job.installation_id)

                check_run_id = await self.github.create_check_run(
                    token=token,
                    owner=job.owner,
                    repo=job.repo,
                    name="Superseded Review",
                    head_sha=job.head_sha,
                    status="in_progress",
                )

                await _run_review_for_job(
                    github=self.github,
                    repo_manager=self.repo_manager,
                    token=token,
                    job=job,
                    correlation_id=correlation_id,
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
                        token = await self.github.get_installation_token(job.installation_id)
                        await self.github.create_check_run(
                            token=token,
                            owner=job.owner,
                            repo=job.repo,
                            name="Superseded Review",
                            head_sha=job.head_sha,
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

        repo_path = await checkout_repo(
            token=token,
            owner=job.owner,
            repo=job.repo,
            ref=job.head_sha,
            base_ref=job.base_sha,
            tmp_dir=str(tmp_dir),
        )

        config = load_config(repo_path / ".superseded.yaml")

        diff = await github.fetch_pr_diff(token, job.owner, job.repo, job.pr_number)
        pr_description = await github.fetch_pr_description(
            token, job.owner, job.repo, job.pr_number
        )

        engine = ReviewEngine.select(config.agent, model=config.model)
        engine.config = config
        result = engine.review(
            diff=diff,
            pr_description=pr_description,
        )

        blocking = result.summary.get("critical", 0) + result.summary.get("important", 0)
        event = "REQUEST_CHANGES" if blocking > 0 else "COMMENT"

        passes_used = sorted({f.pass_name for f in result.findings})
        pass_labels = ", ".join(p.replace("_", " ").title() + " Review" for p in passes_used)

        body = "## Superseded Code Review\n\n"
        if pass_labels:
            body += f"**Passes:** {pass_labels}\n\n"
        for sev, count in result.summary.items():
            body += f"- **{sev}:** {count}\n"

        comments = []
        for f in result.findings:
            body_text = (
                f"**[{f.severity.upper()}] {f.title}** ({f.pass_name})\n\n{f.description}\n\n"
            )
            if f.reasoning:
                body_text += (
                    f"<details><summary>Reasoning</summary>\n\n{f.reasoning}\n\n</details>\n\n"
                )
            body_text += f"**Suggestion:** {f.suggestion}"
            comment: dict = {
                "path": f.file,
                "line": f.end_line,
                "body": body_text,
            }
            if f.line != f.end_line:
                comment["start_line"] = f.line
            comments.append(comment)

        await github.post_review(
            token=token,
            owner=job.owner,
            repo=job.repo,
            pr_number=job.pr_number,
            body=body,
            comments=comments,
            event=event,
        )

        conclusion = "success" if blocking == 0 else "failure"
        title = f"{len(result.findings)} finding(s)"
        if blocking:
            title += f" ({blocking} blocking)"
        summary = (
            f"Review completed. {len(result.findings)} findings across {len(passes_used)} pass(es)."
        )

        await github.create_check_run(
            token=token,
            owner=job.owner,
            repo=job.repo,
            name="Superseded Review",
            head_sha=job.head_sha,
            status="completed",
            conclusion=conclusion,
            title=title,
            summary=summary,
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
    finally:
        repo_manager.cleanup(tmp_dir)
