from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import MagicMock

import pytest

from superseded.server.lifecycle import JsonFormatter, ServerLifecycle
from superseded.server.worker import ReviewJob, ReviewWorker


class _FakeGitHub:
    pass


class _FakeRepoManager:
    def job_dir(self, *args):
        return MagicMock()

    def cleanup(self, *args):
        return None

    def disk_usage(self):
        return 0.5


@pytest.mark.asyncio
async def test_shutdown_drains_in_flight_jobs():
    worker = ReviewWorker(
        github=_FakeGitHub(),
        repo_manager=_FakeRepoManager(),
        max_concurrent=1,
    )
    processed: list[int] = []

    async def fake_process(job: ReviewJob) -> None:
        await asyncio.sleep(0.05)
        processed.append(job.pr_number)

    worker._process = fake_process  # type: ignore[method-assign]
    lifecycle = ServerLifecycle(app=MagicMock(), worker=worker)
    lifecycle._worker_task = asyncio.create_task(worker.run())

    await worker.enqueue(
        ReviewJob(
            installation_id=1,
            owner="o",
            repo="r",
            pr_number=42,
            head_sha="a",
            base_sha="b",
        )
    )
    await asyncio.sleep(0.01)

    await lifecycle.shutdown()

    assert 42 in processed
    assert worker.queue.qsize() == 0


@pytest.mark.asyncio
async def test_shutdown_logs_unprocessed_jobs_when_empty_drain():
    worker = ReviewWorker(
        github=_FakeGitHub(),
        repo_manager=_FakeRepoManager(),
        max_concurrent=1,
    )
    lifecycle = ServerLifecycle(app=MagicMock(), worker=worker)
    lifecycle._worker_task = asyncio.create_task(worker.run())

    await lifecycle.shutdown()
    assert worker.queue.qsize() == 0


def test_json_formatter_serializes_correlation_id_and_extras():
    record = logging.LogRecord(
        name="superseded.server.worker",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="review_started",
        args=None,
        exc_info=None,
    )
    record.correlation_id = "abc12345"
    record.repo = "octocat/hello-world"
    record.pr = 42

    out = JsonFormatter().format(record)
    data = json.loads(out)
    assert data["event"] == "review_started"
    assert data["correlation_id"] == "abc12345"
    assert data["repo"] == "octocat/hello-world"
    assert data["pr"] == 42
    assert data["level"] == "INFO"
