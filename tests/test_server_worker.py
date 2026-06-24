from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from superseded.server.worker import ReviewJob, ReviewWorker


@dataclass
class FakeGitHubApp:
    get_installation_token: AsyncMock = field(
        default_factory=lambda: AsyncMock(return_value="ghp_fake")
    )
    fetch_pr_diff: AsyncMock = field(
        default_factory=lambda: AsyncMock(return_value="diff --git a/x.py")
    )
    fetch_pr_description: AsyncMock = field(
        default_factory=lambda: AsyncMock(return_value="PR desc")
    )
    post_review: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=[1, 2]))
    create_check_run: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=42))


@dataclass
class FakeRepoManager:
    job_dir: MagicMock = field(default_factory=lambda: MagicMock(return_value=Path("/tmp/fake")))
    cleanup: MagicMock = field(default_factory=lambda: MagicMock())
    disk_usage: MagicMock = field(default_factory=lambda: MagicMock(return_value=0.5))


def test_review_job_creation():
    job = ReviewJob(
        installation_id=123,
        owner="octocat",
        repo="hello-world",
        pr_number=42,
        head_sha="abc123",
        base_sha="def456",
    )
    assert job.installation_id == 123
    assert job.pr_number == 42


@pytest.mark.asyncio
async def test_worker_processes_job():
    github = FakeGitHubApp()
    repo_manager = FakeRepoManager()
    worker = ReviewWorker(
        github=github,
        repo_manager=repo_manager,
        max_concurrent=2,
    )

    job = ReviewJob(
        installation_id=123,
        owner="octocat",
        repo="hello-world",
        pr_number=42,
        head_sha="abc123",
        base_sha="def456",
    )

    with patch(
        "superseded.server.worker._run_review_for_job", new_callable=AsyncMock
    ) as mock_review:
        await worker._process(job)

    github.get_installation_token.assert_called_once_with(123)
    github.create_check_run.assert_called_once()
    mock_review.assert_called_once()


@pytest.mark.asyncio
async def test_worker_handles_failure_gracefully():
    github = FakeGitHubApp()
    # First call succeeds (for in_progress), second call also succeeds (for failure update)
    github.create_check_run = AsyncMock(side_effect=[42, 43])
    repo_manager = FakeRepoManager()
    worker = ReviewWorker(
        github=github,
        repo_manager=repo_manager,
        max_concurrent=1,
    )

    job = ReviewJob(
        installation_id=123,
        owner="octocat",
        repo="hello-world",
        pr_number=42,
        head_sha="abc123",
        base_sha="def456",
    )

    with patch(
        "superseded.server.worker._run_review_for_job",
        new_callable=AsyncMock,
        side_effect=RuntimeError("review failed"),
    ):
        # Should not raise — errors are caught and logged
        await worker._process(job)

    # Two calls: in_progress + failure conclusion
    assert github.create_check_run.call_count == 2
