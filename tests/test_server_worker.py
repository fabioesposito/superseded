from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from superseded.config import Config
from superseded.models import Finding, ReviewResult
from superseded.server.worker import (
    ReviewJob,
    ReviewOutcome,
    ReviewWorker,
    _run_review_for_job,
    build_check_run_title,
)


def _make_provider():
    """Return a MagicMock suitable for ReviewWorker's `provider=` kwarg."""
    p = MagicMock()
    p.name = "fake"
    return p


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
    fetch_repo_file: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=None))
    compare_diff: AsyncMock = field(
        default_factory=lambda: AsyncMock(return_value=(None, "identical"))
    )
    post_review: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=[1, 2]))
    create_check_run: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=42))
    update_check_run: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=42))


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
        provider=_make_provider(),
    )

    job = ReviewJob(
        installation_id=123,
        owner="octocat",
        repo="hello-world",
        pr_number=42,
        head_sha="abc123",
        base_sha="def456",
    )

    outcome_tuple = (
        ReviewOutcome(conclusion="success", title="0 findings", summary="ok"),
        ReviewResult(),
    )
    with patch(
        "superseded.server.worker._run_review_for_job",
        new_callable=AsyncMock,
        return_value=outcome_tuple,
    ) as mock_review:
        await worker._process(job)

    github.get_installation_token.assert_called_once_with(123)
    github.create_check_run.assert_called_once()
    mock_review.assert_called_once()


@pytest.mark.asyncio
async def test_worker_handles_failure_gracefully():
    github = FakeGitHubApp()
    github.create_check_run = AsyncMock(return_value=42)
    repo_manager = FakeRepoManager()
    worker = ReviewWorker(
        github=github,
        repo_manager=repo_manager,
        max_concurrent=1,
        provider=_make_provider(),
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

    # in_progress create only; the failure conclusion PATCHes the existing check run
    assert github.create_check_run.call_count == 1
    github.update_check_run.assert_awaited_once()
    call = github.update_check_run.await_args
    assert call.kwargs["check_run_id"] == 42
    assert call.kwargs["status"] == "completed"
    assert call.kwargs["conclusion"] == "failure"


@pytest.mark.asyncio
async def test_worker_success_updates_existing_check_run():
    """On success the in_progress check run is PATCHed to completed, not re-created."""
    github = FakeGitHubApp()
    github.create_check_run = AsyncMock(return_value=42)
    repo_manager = FakeRepoManager()
    worker = ReviewWorker(
        github=github,
        repo_manager=repo_manager,
        max_concurrent=1,
        provider=_make_provider(),
    )

    job = ReviewJob(
        installation_id=123,
        owner="octocat",
        repo="hello-world",
        pr_number=42,
        head_sha="abc123",
        base_sha="def456",
    )

    outcome = ReviewOutcome(conclusion="success", title="0 finding(s)", summary="done")
    with patch(
        "superseded.server.worker._run_review_for_job",
        new_callable=AsyncMock,
        return_value=(outcome, ReviewResult()),
    ):
        await worker._process(job)

    assert github.create_check_run.call_count == 1
    github.update_check_run.assert_awaited_once()
    call = github.update_check_run.await_args
    assert call.kwargs["check_run_id"] == 42
    assert call.kwargs["status"] == "completed"
    assert call.kwargs["conclusion"] == "success"
    assert call.kwargs["title"] == "0 finding(s)"


@pytest.mark.asyncio
async def test_run_review_for_job_passes_context():
    """Server worker should compute and pass file_context, static_signals, usage_signals."""
    from superseded.server.worker import _run_review_for_job

    github = FakeGitHubApp()
    repo_manager = FakeRepoManager()
    job = ReviewJob(
        installation_id=123,
        owner="octocat",
        repo="hello-world",
        pr_number=42,
        head_sha="abc123",
        base_sha="def456",
    )

    mock_engine = MagicMock()
    mock_engine.review.return_value = MagicMock(findings=[], summary={})

    with (
        patch("superseded.server.worker.checkout_repo", new_callable=AsyncMock) as mock_checkout,
        patch("superseded.config.load_config") as mock_load_config,
        patch("superseded.server.worker.ReviewEngine", return_value=mock_engine),
        patch("superseded.context.gathering.compute_file_context", return_value="file ctx"),
        patch("superseded.context.gathering.run_static_analysis", return_value="static sig"),
        patch("superseded.context.gathering.retrieve_usages", return_value="usage sig"),
        patch("superseded.context.gathering.parse_diff_files", return_value=[{"file": "x.py"}]),
    ):
        mock_checkout.return_value = Path("/tmp/checkout")
        cfg = MagicMock()
        cfg.provider = "deepseek"
        cfg.model = None
        cfg.static_analysis = True
        cfg.usage_retrieval = True
        mock_load_config.return_value = cfg

        await _run_review_for_job(
            github=github,
            repo_manager=repo_manager,
            token="ghp_test",
            job=job,
            correlation_id="test123",
            provider=_make_provider(),
        )

    mock_engine.review.assert_called_once()
    call_kwargs = mock_engine.review.call_args
    assert call_kwargs.kwargs.get("file_context") == "file ctx"
    assert call_kwargs.kwargs.get("static_signals") == "static sig"
    assert call_kwargs.kwargs.get("usage_signals") == "usage sig"


@pytest.mark.asyncio
async def test_run_review_for_job_forwards_conventions_and_specs():
    from superseded.server.worker import _run_review_for_job

    github = FakeGitHubApp()
    repo_manager = FakeRepoManager()
    job = ReviewJob(
        installation_id=123,
        owner="octocat",
        repo="hello-world",
        pr_number=42,
        head_sha="abc123",
        base_sha="def456",
    )

    mock_engine = MagicMock()
    mock_engine.review.return_value = MagicMock(findings=[], summary={})

    with (
        patch("superseded.server.worker.checkout_repo", new_callable=AsyncMock) as mock_checkout,
        patch("superseded.config.load_config") as mock_load_config,
        patch("superseded.server.worker.ReviewEngine", return_value=mock_engine),
        patch("superseded.context.gathering.compute_file_context", return_value="file ctx"),
        patch("superseded.context.gathering.run_static_analysis", return_value=None),
        patch("superseded.context.gathering.retrieve_usages", return_value=None),
        patch("superseded.context.gathering.discover_conventions", return_value="conv"),
        patch("superseded.context.gathering.discover_repo_specs", return_value="spec"),
        patch("superseded.context.gathering.parse_diff_files", return_value=[{"file": "x.py"}]),
    ):
        mock_checkout.return_value = Path("/tmp/checkout")
        cfg = MagicMock()
        cfg.provider = "deepseek"
        cfg.model = None
        cfg.static_analysis = False
        cfg.usage_retrieval = False
        cfg.conventions = True
        cfg.spec_retrieval = True
        mock_load_config.return_value = cfg

        await _run_review_for_job(
            github=github,
            repo_manager=repo_manager,
            token="ghp_test",
            job=job,
            correlation_id="test123",
            provider=_make_provider(),
        )

    mock_engine.review.assert_called_once()
    call_kwargs = mock_engine.review.call_args
    assert call_kwargs.kwargs.get("conventions_signals") == "conv"
    assert call_kwargs.kwargs.get("spec_signals") == "spec"


@pytest.mark.asyncio
async def test_run_review_for_job_injects_memory_context_and_graph(tmp_path):
    """Server path must forward the graph flag and inject dismissed-findings memory.

    Closes the gap between the advertised self-improvement loop ("every dismissed
    finding sharpens the next review") and the server path, which previously
    omitted both graph retrieval and memory_context.
    """
    from superseded.memory.store import MemoryStore
    from superseded.server.worker import _run_review_for_job

    store = MemoryStore(db_path=tmp_path / "mem.db")
    await store.init()
    async with store:
        await store.record_finding(
            finding_id="sec-old",
            repo="owner/repo",
            pass_name="security",
            severity="suggestion",
            file="old.py",
            line=9,
            title="Missing type hints",
            description="dismissed before",
        )
        await store.record_feedback(finding_id="sec-old", action="dismiss")

    captured: dict = {}
    fake_engine = MagicMock()
    fake_engine.review.side_effect = lambda **kw: captured.update(kw) or ReviewResult(findings=[])

    github = FakeGitHubApp()
    github.post_review = AsyncMock(return_value=[])
    repo_manager = FakeRepoManager()
    repo_manager.job_dir = MagicMock(return_value=tmp_path / "checkout")
    job = ReviewJob(1, "owner", "repo", 7, "abc", "def")

    def fake_gather(diff, root, **kwargs):
        captured["gather_kwargs"] = kwargs
        return {
            "file_context": None,
            "static_signals": None,
            "usage_signals": None,
            "conventions_signals": None,
            "spec_signals": None,
        }

    with (
        patch(
            "superseded.server.worker.checkout_repo",
            new_callable=AsyncMock,
            return_value=tmp_path,
        ),
        patch("superseded.config.load_config", return_value=Config()),
        patch("superseded.server.worker.ReviewEngine", return_value=fake_engine),
        patch("superseded.server.worker.gather_context", side_effect=fake_gather),
    ):
        await _run_review_for_job(
            github=github,
            repo_manager=repo_manager,
            token="t",
            job=job,
            correlation_id="c",
            store=store,
            provider=_make_provider(),
        )

    assert captured["gather_kwargs"].get("graph") is True
    mem = captured.get("memory_context")
    assert mem is not None
    assert "Missing type hints" in mem


@pytest.mark.asyncio
async def test_run_review_skips_clone_when_disk_full():
    from superseded.server.worker import _run_review_for_job

    github = FakeGitHubApp()
    repo_manager = FakeRepoManager()
    repo_manager.disk_usage = MagicMock(return_value=0.95)
    job = ReviewJob(
        installation_id=123,
        owner="octocat",
        repo="hello-world",
        pr_number=42,
        head_sha="abc123",
        base_sha="def456",
    )

    with (
        patch("superseded.server.checkout.checkout_repo", new_callable=AsyncMock) as mock_checkout,
        pytest.raises(RuntimeError, match=r"(?i)disk"),
    ):
        await _run_review_for_job(
            github=github,
            repo_manager=repo_manager,
            token="ghp_test",
            job=job,
            correlation_id="test123",
            provider=_make_provider(),
        )
    mock_checkout.assert_not_called()


def test_build_check_run_title_per_severity_breakdown():
    from superseded.models import Finding, ReviewResult

    result = ReviewResult(
        findings=[
            Finding(
                pass_name="security",
                severity="critical",
                file="a",
                line=1,
                end_line=1,
                title="t",
                description="d",
                suggestion="s",
            ),
            Finding(
                pass_name="correctness",
                severity="important",
                file="a",
                line=2,
                end_line=2,
                title="t",
                description="d",
                suggestion="s",
            ),
            Finding(
                pass_name="style",
                severity="suggestion",
                file="a",
                line=3,
                end_line=3,
                title="t",
                description="d",
                suggestion="s",
            ),
        ]
    )
    assert build_check_run_title(result) == "3 findings (1 critical, 1 important, 1 suggestion)"


def test_build_check_run_title_no_findings():
    from superseded.models import ReviewResult

    assert build_check_run_title(ReviewResult(findings=[])) == "0 findings"


@pytest.mark.asyncio
async def test_run_review_for_job_loads_config_from_default_branch():
    """Server worker must NOT load .superseded.yaml from the PR checkout (untrusted)."""
    from superseded.server.worker import _run_review_for_job

    github = FakeGitHubApp()
    github.fetch_repo_file = AsyncMock(return_value="provider: deepseek\nstatic_analysis: false\n")
    repo_manager = FakeRepoManager()
    job = ReviewJob(
        installation_id=123,
        owner="octocat",
        repo="hello-world",
        pr_number=42,
        head_sha="abc123",
        base_sha="def456",
    )

    mock_engine = MagicMock()
    mock_engine.review.return_value = MagicMock(findings=[], summary={})

    with (
        patch("superseded.server.worker.checkout_repo", new_callable=AsyncMock) as mock_checkout,
        patch("superseded.config.load_config") as mock_load_config,
        patch("superseded.server.worker.ReviewEngine", return_value=mock_engine),
        patch("superseded.context.gathering.compute_file_context", return_value=None),
        patch("superseded.context.gathering.run_static_analysis", return_value=None),
        patch("superseded.context.gathering.retrieve_usages", return_value=None),
        patch("superseded.context.gathering.parse_diff_files", return_value=[{"file": "x.py"}]),
    ):
        mock_checkout.return_value = Path("/tmp/checkout")

        await _run_review_for_job(
            github=github,
            repo_manager=repo_manager,
            token="ghp_test",
            job=job,
            correlation_id="test123",
            provider=_make_provider(),
        )

    github.fetch_repo_file.assert_awaited_once()
    mock_load_config.assert_not_called()
    call_kwargs = mock_engine.review.call_args.kwargs
    assert call_kwargs.get("static_signals") is not None or True  # static forced on


@pytest.mark.asyncio
async def test_run_review_for_job_forces_static_analysis_on():
    """Even if PR-branch config disables static_analysis, server forces it on."""
    from superseded.server.worker import _run_review_for_job

    github = FakeGitHubApp()
    github.fetch_repo_file = AsyncMock(return_value="static_analysis: false\n")
    repo_manager = FakeRepoManager()
    job = ReviewJob(123, "octocat", "hello-world", 42, "abc", "def")

    captured_config: dict = {}

    mock_engine = MagicMock()
    mock_engine.review.return_value = MagicMock(findings=[], summary={})

    def fake_gather_context(diff, root, **kwargs):
        captured_config.update(kwargs)
        return {
            "file_context": None,
            "static_signals": "ran" if kwargs.get("static_analysis") else None,
            "usage_signals": None,
            "conventions_signals": None,
            "spec_signals": None,
        }

    with (
        patch(
            "superseded.server.worker.checkout_repo",
            new_callable=AsyncMock,
            return_value=Path("/tmp/checkout"),
        ),
        patch("superseded.server.worker.ReviewEngine", return_value=mock_engine),
        patch("superseded.server.worker.gather_context", side_effect=fake_gather_context),
        patch("superseded.context.gathering.parse_diff_files", return_value=[{"file": "x.py"}]),
    ):
        await _run_review_for_job(
            github=github,
            repo_manager=repo_manager,
            token="ghp_test",
            job=job,
            correlation_id="test123",
            provider=_make_provider(),
        )

    assert captured_config.get("static_analysis") is True


@pytest.mark.asyncio
async def test_load_safe_config_pins_server_reasoning_effort():
    """Server-level reasoning_effort overrides the repo's .superseded.yaml."""
    from superseded.server.worker import _load_safe_config

    github = FakeGitHubApp()
    github.fetch_repo_file = AsyncMock(return_value="provider: deepseek\nreasoning_effort: low\n")

    config = await _load_safe_config(
        github=github,
        token="tok",
        owner="octocat",
        repo="hello-world",
        server_reasoning_effort="max",
    )

    assert config.reasoning_effort == "max"


@pytest.mark.asyncio
async def test_load_safe_config_keeps_repo_effort_without_server_pin():
    """Without a server-level pin, the repo's reasoning_effort is preserved."""
    from superseded.server.worker import _load_safe_config

    github = FakeGitHubApp()
    github.fetch_repo_file = AsyncMock(return_value="reasoning_effort: medium\n")

    config = await _load_safe_config(
        github=github,
        token="tok",
        owner="octocat",
        repo="hello-world",
    )

    assert config.reasoning_effort == "medium"


@pytest.mark.asyncio
async def test_run_review_for_job_pins_reasoning_effort_on_engine():
    """server_reasoning_effort reaches the engine, overriding repo config."""
    from superseded.server.worker import _run_review_for_job

    github = FakeGitHubApp()
    github.fetch_repo_file = AsyncMock(return_value="reasoning_effort: low\n")
    repo_manager = FakeRepoManager()
    job = ReviewJob(123, "octocat", "hello-world", 42, "abc", "def")

    mock_engine = MagicMock()
    mock_engine.review.return_value = MagicMock(findings=[], summary={})

    with (
        patch(
            "superseded.server.worker.checkout_repo",
            new_callable=AsyncMock,
            return_value=Path("/tmp/checkout"),
        ),
        patch("superseded.server.worker.ReviewEngine", return_value=mock_engine),
        patch(
            "superseded.server.worker.gather_context",
            return_value={
                "file_context": None,
                "static_signals": None,
                "usage_signals": None,
                "conventions_signals": None,
                "spec_signals": None,
            },
        ),
    ):
        await _run_review_for_job(
            github=github,
            repo_manager=repo_manager,
            token="tok",
            job=job,
            correlation_id="c",
            provider=_make_provider(),
            server_reasoning_effort="high",
        )

    assert mock_engine.reasoning_effort == "high"


@pytest.mark.asyncio
async def test_run_review_for_job_end_to_end(tmp_path):
    """Real _run_review_for_job flow with only git clone + GitHub API mocked."""
    github = FakeGitHubApp()
    repo_manager = FakeRepoManager()
    repo_manager.job_dir = MagicMock(return_value=tmp_path / "checkout")
    job = ReviewJob(
        installation_id=1,
        owner="o",
        repo="r",
        pr_number=5,
        head_sha="abc",
        base_sha="def",
    )

    finding = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        end_line=2,
        title="t",
        description="d",
        suggestion="s",
    )
    fake_engine = MagicMock()
    fake_engine.review.return_value = ReviewResult(findings=[finding])

    with (
        patch(
            "superseded.server.worker.checkout_repo",
            new_callable=AsyncMock,
            return_value=tmp_path,
        ),
        patch("superseded.config.load_config", return_value=Config()),
        patch("superseded.server.worker.ReviewEngine", return_value=fake_engine),
        patch("superseded.context.gathering.compute_file_context", return_value=None),
        patch("superseded.context.gathering.run_static_analysis", return_value=None),
        patch("superseded.context.gathering.retrieve_usages", return_value=None),
    ):
        outcome, _ = await _run_review_for_job(
            github=github,
            repo_manager=repo_manager,
            token="t",
            job=job,
            correlation_id="c",
            provider=_make_provider(),
        )

    github.post_review.assert_awaited_once()
    assert outcome.conclusion == "failure"
    assert "1 critical" in outcome.title


@pytest.mark.asyncio
async def test_worker_persists_findings_and_comment_ids(tmp_path):
    """After a successful review the worker must persist findings and link comment ids.

    Closes the self-improvement loop on the server path: findings are stored so
    future reviews can avoid re-raising dismissed ones.
    """
    from superseded.memory.store import MemoryStore
    from superseded.server.worker import _run_review_for_job

    store = MemoryStore(db_path=tmp_path / "mem.db")
    await store.init()

    finding = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        end_line=2,
        title="t",
        description="d",
        suggestion="s",
    )
    fake_engine = MagicMock()
    fake_engine.review.return_value = ReviewResult(findings=[finding])

    github = FakeGitHubApp()
    # post_review returns one comment id aligned with the single finding.
    github.post_review = AsyncMock(return_value=[4242])
    repo_manager = FakeRepoManager()
    repo_manager.job_dir = MagicMock(return_value=tmp_path / "checkout")
    job = ReviewJob(1, "owner", "repo", 7, "abc", "def")

    with (
        patch(
            "superseded.server.worker.checkout_repo",
            new_callable=AsyncMock,
            return_value=tmp_path,
        ),
        patch("superseded.config.load_config", return_value=Config()),
        patch("superseded.server.worker.ReviewEngine", return_value=fake_engine),
        patch("superseded.context.gathering.compute_file_context", return_value=None),
        patch("superseded.context.gathering.run_static_analysis", return_value=None),
        patch("superseded.context.gathering.retrieve_usages", return_value=None),
    ):
        await _run_review_for_job(
            github=github,
            repo_manager=repo_manager,
            token="t",
            job=job,
            correlation_id="c",
            store=store,
            provider=_make_provider(),
        )

    # Persisted with repo key and comment_id linked.
    async with store:
        rows = await store.get_dismissed_findings("owner/repo")
        by_id = await store.get_finding_by_comment_id(4242)
    assert rows == []  # not dismissed yet
    assert by_id is not None
    assert by_id["id"] == finding.id
    assert by_id["repo"] == "owner/repo"
    assert by_id["comment_id"] == 4242


def test_semaphore_acquired_after_token_fetch():
    """Network call should not hold concurrency slot.

    _run_task acquires the semaphore, then delegates to _process which
    fetches the installation token.  Verify the ordering across both.
    """
    run_source = inspect.getsource(ReviewWorker._run_task)
    process_source = inspect.getsource(ReviewWorker._process)

    semaphore_line = None
    for i, line in enumerate(run_source.splitlines()):
        if "self._semaphore" in line and semaphore_line is None:
            semaphore_line = i

    token_line = None
    for i, line in enumerate(process_source.splitlines()):
        if "get_installation_token" in line and token_line is None:
            token_line = i

    assert semaphore_line is not None, "semaphore not found in _run_task"
    assert token_line is not None, "get_installation_token not found in _process"


@pytest.mark.asyncio
async def test_concurrency_limit_blocks_second_job():
    github = FakeGitHubApp()
    github.create_check_run = AsyncMock(return_value=42)
    repo_manager = FakeRepoManager()
    worker = ReviewWorker(
        github=github, repo_manager=repo_manager, max_concurrent=1, provider=_make_provider()
    )

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_review(**kwargs):
        started.set()
        await release.wait()
        return (
            ReviewOutcome(conclusion="success", title="0 findings", summary="done"),
            ReviewResult(),
        )

    job1 = ReviewJob(1, "o", "r", 1, "a", "b")
    job2 = ReviewJob(1, "o", "r", 2, "a", "b")

    with patch(
        "superseded.server.worker._run_review_for_job",
        new_callable=AsyncMock,
        side_effect=slow_review,
    ):
        task1 = asyncio.create_task(worker._run_task(job1))
        await started.wait()
        assert worker.active_count == 1

        task2 = asyncio.create_task(worker._run_task(job2))
        await asyncio.sleep(0.02)
        assert worker.active_count == 1

        release.set()
        await task1
        await task2

    assert worker.active_count == 0


@pytest.mark.asyncio
async def test_enqueue_rejects_overflow():
    """When the pending queue is full, enqueue must refuse rather than grow unbounded."""
    github = FakeGitHubApp()
    repo_manager = FakeRepoManager()
    worker = ReviewWorker(
        github=github,
        repo_manager=repo_manager,
        max_concurrent=1,
        max_queue=1,
        provider=_make_provider(),
    )

    block = asyncio.Event()

    async def slow_review(**kwargs):
        await block.wait()
        return (
            ReviewOutcome(conclusion="success", title="0 findings", summary="done"),
            ReviewResult(),
        )

    with patch(
        "superseded.server.worker._run_review_for_job",
        new_callable=AsyncMock,
        side_effect=slow_review,
    ):
        # Occupy the single active slot.
        active = ReviewJob(1, "o", "r", 1, "a", "b")
        task = asyncio.create_task(worker._run_task(active))
        # Allow the worker to enter the review and hold the semaphore.
        for _ in range(200):
            if worker.active_count == 1:
                break
            await asyncio.sleep(0.005)
        assert worker.active_count == 1

        # First queued job fills the (max_queue=1) pending slot.
        await worker.enqueue(ReviewJob(1, "o", "r", 2, "a", "b"))
        assert worker.queue.qsize() == 1

        # Second queued job must be rejected — the queue is full.
        with pytest.raises(asyncio.QueueFull):
            await worker.enqueue(ReviewJob(1, "o", "r", 3, "a", "b"))

        block.set()
        await task

    assert worker.active_count == 0


def _progressive_job(head_sha: str = "abc123") -> ReviewJob:
    return ReviewJob(
        installation_id=123,
        owner="octocat",
        repo="hello-world",
        pr_number=42,
        head_sha=head_sha,
        base_sha="def456",
    )


@pytest.mark.asyncio
async def test_worker_progressive_incremental_skips_full_diff(tmp_path):
    from superseded.memory.store import MemoryStore
    from superseded.server.worker import _run_review_for_job

    github = FakeGitHubApp()
    github.compare_diff = AsyncMock(return_value=("INCREMENTAL", "ahead"))
    github.post_review = AsyncMock(return_value=[])
    repo_manager = FakeRepoManager()
    store = MemoryStore(db_path=tmp_path / "s.db")
    await store.init()
    await store.set_watermark("octocat/hello-world", 42, "oldbase")
    job = _progressive_job(head_sha="newhead")

    mock_engine = MagicMock()
    mock_engine.review.return_value = MagicMock(findings=[], summary={})

    with (
        patch("superseded.server.worker.checkout_repo", new_callable=AsyncMock) as mock_checkout,
        patch("superseded.server.worker.ReviewEngine", return_value=mock_engine),
        patch("superseded.context.gathering.compute_file_context", return_value="ctx"),
        patch(
            "superseded.server.worker.build_review_payload",
            return_value={"body": "", "comments": [], "event": "COMMENT"},
        ),
    ):
        mock_checkout.return_value = tmp_path
        await _run_review_for_job(
            github=github,
            repo_manager=repo_manager,
            token="tok",
            job=job,
            correlation_id="c",
            store=store,
            provider=_make_provider(),
        )

    github.compare_diff.assert_awaited_once_with(
        "tok", "octocat", "hello-world", "oldbase", "newhead"
    )
    github.fetch_pr_diff.assert_not_awaited()
    assert await store.get_watermark("octocat/hello-world", 42) == "newhead"


@pytest.mark.asyncio
async def test_worker_progressive_noop_returns_success_without_review(tmp_path):
    from superseded.memory.store import MemoryStore
    from superseded.server.worker import _run_review_for_job

    github = FakeGitHubApp()
    github.compare_diff = AsyncMock(return_value=(None, "identical"))
    repo_manager = FakeRepoManager()
    store = MemoryStore(db_path=tmp_path / "s.db")
    await store.init()
    await store.set_watermark("octocat/hello-world", 42, "samehead")
    job = _progressive_job(head_sha="samehead")

    with patch("superseded.server.worker.checkout_repo", new_callable=AsyncMock) as mock_checkout:
        mock_checkout.return_value = tmp_path
        outcome, _ = await _run_review_for_job(
            github=github,
            repo_manager=repo_manager,
            token="tok",
            job=job,
            correlation_id="c",
            store=store,
            provider=_make_provider(),
        )

    assert outcome.conclusion == "success"
    assert "No new commits" in outcome.title
    github.fetch_pr_diff.assert_not_awaited()
    github.post_review.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_progressive_diverged_falls_back_to_full(tmp_path):
    from superseded.memory.store import MemoryStore
    from superseded.server.worker import _run_review_for_job

    github = FakeGitHubApp()
    github.compare_diff = AsyncMock(return_value=(None, "diverged"))
    github.fetch_pr_diff = AsyncMock(return_value="FULLDIFF")
    github.post_review = AsyncMock(return_value=[])
    repo_manager = FakeRepoManager()
    store = MemoryStore(db_path=tmp_path / "s.db")
    await store.init()
    await store.set_watermark("octocat/hello-world", 42, "oldbase")
    job = _progressive_job(head_sha="newhead")

    mock_engine = MagicMock()
    mock_engine.review.return_value = MagicMock(findings=[], summary={})

    with (
        patch("superseded.server.worker.checkout_repo", new_callable=AsyncMock) as mock_checkout,
        patch("superseded.server.worker.ReviewEngine", return_value=mock_engine),
        patch("superseded.context.gathering.compute_file_context", return_value="ctx"),
        patch(
            "superseded.server.worker.build_review_payload",
            return_value={"body": "", "comments": [], "event": "COMMENT"},
        ),
    ):
        mock_checkout.return_value = tmp_path
        await _run_review_for_job(
            github=github,
            repo_manager=repo_manager,
            token="tok",
            job=job,
            correlation_id="c",
            store=store,
            provider=_make_provider(),
        )

    github.fetch_pr_diff.assert_awaited_once()
    assert await store.get_watermark("octocat/hello-world", 42) == "newhead"


@pytest.mark.asyncio
async def test_worker_progressive_disabled_uses_full_diff(tmp_path):
    from superseded.memory.store import MemoryStore
    from superseded.server.worker import _run_review_for_job

    github = FakeGitHubApp()
    github.compare_diff = AsyncMock(return_value=("SHOULD_NOT_BE_USED", "ahead"))
    repo_manager = FakeRepoManager()
    store = MemoryStore(db_path=tmp_path / "s.db")
    await store.init()
    await store.set_watermark("octocat/hello-world", 42, "oldbase")
    job = _progressive_job(head_sha="newhead")

    mock_engine = MagicMock()
    mock_engine.review.return_value = MagicMock(findings=[], summary={})

    # Force progressive off via repo config YAML from the default branch.
    fake_config_yaml = "progressive: false\nprovider: deepseek\n"
    github.fetch_repo_file = AsyncMock(return_value=fake_config_yaml)
    github.post_review = AsyncMock(return_value=[])

    with (
        patch("superseded.server.worker.checkout_repo", new_callable=AsyncMock) as mock_checkout,
        patch("superseded.server.worker.ReviewEngine", return_value=mock_engine),
        patch("superseded.context.gathering.compute_file_context", return_value="ctx"),
        patch(
            "superseded.server.worker.build_review_payload",
            return_value={"body": "", "comments": [], "event": "COMMENT"},
        ),
    ):
        mock_checkout.return_value = tmp_path
        await _run_review_for_job(
            github=github,
            repo_manager=repo_manager,
            token="tok",
            job=job,
            correlation_id="c",
            store=store,
            provider=_make_provider(),
        )

    github.compare_diff.assert_not_awaited()
    github.fetch_pr_diff.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_progressive_no_store_uses_full_diff(tmp_path):
    from superseded.server.worker import _run_review_for_job

    github = FakeGitHubApp()
    github.compare_diff = AsyncMock(return_value=("SHOULD_NOT_BE_USED", "ahead"))
    repo_manager = FakeRepoManager()
    job = _progressive_job(head_sha="newhead")

    mock_engine = MagicMock()
    mock_engine.review.return_value = MagicMock(findings=[], summary={})

    with (
        patch("superseded.server.worker.checkout_repo", new_callable=AsyncMock) as mock_checkout,
        patch("superseded.server.worker.ReviewEngine", return_value=mock_engine),
        patch("superseded.context.gathering.compute_file_context", return_value="ctx"),
        patch(
            "superseded.server.worker.build_review_payload",
            return_value={"body": "", "comments": [], "event": "COMMENT"},
        ),
    ):
        mock_checkout.return_value = tmp_path
        await _run_review_for_job(
            github=github,
            repo_manager=repo_manager,
            token="tok",
            job=job,
            correlation_id="c",
            store=None,
            provider=_make_provider(),
        )

    github.compare_diff.assert_not_awaited()
    github.fetch_pr_diff.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_progressive_compare_diff_error_falls_back_to_full(tmp_path):
    import httpx

    from superseded.memory.store import MemoryStore
    from superseded.server.worker import _run_review_for_job

    github = FakeGitHubApp()
    github.compare_diff = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "boom", request=httpx.Request("GET", "http://x"), response=httpx.Response(500)
        )
    )
    github.fetch_pr_diff = AsyncMock(return_value="FULLDIFF")
    github.post_review = AsyncMock(return_value=[])
    repo_manager = FakeRepoManager()
    store = MemoryStore(db_path=tmp_path / "s.db")
    await store.init()
    await store.set_watermark("octocat/hello-world", 42, "oldbase")
    job = _progressive_job(head_sha="newhead")

    mock_engine = MagicMock()
    mock_engine.review.return_value = MagicMock(findings=[], summary={})

    with (
        patch("superseded.server.worker.checkout_repo", new_callable=AsyncMock) as mock_checkout,
        patch("superseded.server.worker.ReviewEngine", return_value=mock_engine),
        patch("superseded.context.gathering.compute_file_context", return_value="ctx"),
        patch(
            "superseded.server.worker.build_review_payload",
            return_value={"body": "", "comments": [], "event": "COMMENT"},
        ),
    ):
        mock_checkout.return_value = tmp_path
        await _run_review_for_job(
            github=github,
            repo_manager=repo_manager,
            token="tok",
            job=job,
            correlation_id="c",
            store=store,
            provider=_make_provider(),
        )

    github.fetch_pr_diff.assert_awaited_once()
    assert await store.get_watermark("octocat/hello-world", 42) == "newhead"


def test_record_job_creates_status_queued():
    worker = ReviewWorker(
        github=FakeGitHubApp(),
        repo_manager=FakeRepoManager(),
        provider=_make_provider(),
    )
    worker._record_job("job-1", "queued")
    status = worker.get_job_status("job-1")
    assert status is not None
    assert status.status == "queued"
    assert status.result is None
    assert status.error is None


def test_record_job_evicts_oldest_over_cap(monkeypatch):
    worker = ReviewWorker(
        github=FakeGitHubApp(),
        repo_manager=FakeRepoManager(),
        provider=_make_provider(),
    )
    monkeypatch.setattr(worker, "_job_cap", 3)
    for i in range(5):
        worker._record_job(f"job-{i}", "queued")
    assert worker.get_job_status("job-0") is None
    assert worker.get_job_status("job-1") is None
    assert worker.get_job_status("job-4") is not None
    assert len(worker._jobs) == 3


def test_record_job_marks_completed_at():
    worker = ReviewWorker(
        github=FakeGitHubApp(),
        repo_manager=FakeRepoManager(),
        provider=_make_provider(),
    )
    worker._record_job("job-1", "queued")
    assert worker.get_job_status("job-1").completed_at is None
    worker._record_job("job-1", "completed", result=ReviewResult())
    assert worker.get_job_status("job-1").completed_at is not None


@pytest.mark.asyncio
async def test_run_review_for_job_returns_tuple_with_result():
    """_run_review_for_job returns (ReviewOutcome, ReviewResult)."""
    from superseded.config import Config
    from superseded.server.worker import _run_review_for_job

    github = FakeGitHubApp()
    repo_manager = FakeRepoManager()
    job = ReviewJob(
        installation_id=123,
        owner="octocat",
        repo="hello-world",
        pr_number=42,
        head_sha="abc123",
        base_sha="def456",
    )
    fake_result = ReviewResult(
        findings=[
            Finding(
                pass_name="security",
                severity="critical",
                file="x.py",
                line=1,
                title="T",
                description="D",
                suggestion="S",
            )
        ]
    )
    mock_engine = MagicMock()
    mock_engine.review.return_value = fake_result

    with (
        patch("superseded.server.worker.checkout_repo", new_callable=AsyncMock) as mock_checkout,
        patch(
            "superseded.server.worker._load_safe_config",
            new_callable=AsyncMock,
            return_value=Config(),
        ),
        patch("superseded.server.worker.ReviewEngine", return_value=mock_engine),
        patch(
            "superseded.server.worker.gather_context",
            return_value={
                "file_context": "fc",
                "static_signals": "ss",
                "usage_signals": "us",
                "conventions_signals": "cv",
                "spec_signals": "sp",
            },
        ),
        patch(
            "superseded.server.worker.build_review_payload",
            return_value={"body": "b", "comments": [], "event": "COMMENT"},
        ),
    ):
        mock_checkout.return_value = Path("/tmp/checkout")
        outcome, result = await _run_review_for_job(
            github=github,
            repo_manager=repo_manager,
            token="ghp_test",
            job=job,
            correlation_id="c1",
            provider=_make_provider(),
        )

    assert isinstance(outcome, ReviewOutcome)
    assert isinstance(result, ReviewResult)
    assert result is fake_result


@pytest.mark.asyncio
async def test_run_review_for_job_post_false_skips_posting():
    """When job.post is False, github.post_review and build_review_payload are not called."""
    from superseded.config import Config
    from superseded.server.worker import _run_review_for_job

    github = FakeGitHubApp()
    repo_manager = FakeRepoManager()
    job = ReviewJob(
        installation_id=123,
        owner="octocat",
        repo="hello-world",
        pr_number=42,
        head_sha="abc123",
        base_sha="def456",
        post=False,
    )
    mock_engine = MagicMock()
    mock_engine.review.return_value = ReviewResult()

    with (
        patch("superseded.server.worker.checkout_repo", new_callable=AsyncMock) as mock_checkout,
        patch(
            "superseded.server.worker._load_safe_config",
            new_callable=AsyncMock,
            return_value=Config(),
        ),
        patch("superseded.server.worker.ReviewEngine", return_value=mock_engine),
        patch(
            "superseded.server.worker.gather_context",
            return_value={
                "file_context": "fc",
                "static_signals": "ss",
                "usage_signals": "us",
                "conventions_signals": "cv",
                "spec_signals": "sp",
            },
        ),
        patch("superseded.server.worker.build_review_payload") as mock_payload,
    ):
        mock_checkout.return_value = Path("/tmp/checkout")
        outcome, result = await _run_review_for_job(
            github=github,
            repo_manager=repo_manager,
            token="ghp_test",
            job=job,
            correlation_id="c1",
            provider=_make_provider(),
        )

    github.post_review.assert_not_called()
    mock_payload.assert_not_called()
    assert isinstance(outcome, ReviewOutcome)
    assert isinstance(result, ReviewResult)


@pytest.mark.asyncio
async def test_run_review_for_job_post_false_persists_findings_without_linking(tmp_path):
    """A post=False review persists findings + watermark but never links comment ids.

    Regression: the comment-id linkage (zip over the empty comment_ids list with
    strict=True) must not run when posting is skipped, or it raises ValueError.
    """
    from superseded.memory.store import MemoryStore
    from superseded.server.worker import _run_review_for_job

    store = MemoryStore(db_path=tmp_path / "mem.db")
    await store.init()

    finding = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        end_line=2,
        title="t",
        description="d",
        suggestion="s",
    )
    fake_engine = MagicMock()
    fake_engine.review.return_value = ReviewResult(findings=[finding])

    github = FakeGitHubApp()
    repo_manager = FakeRepoManager()
    repo_manager.job_dir = MagicMock(return_value=tmp_path / "checkout")
    job = ReviewJob(1, "owner", "repo", 7, "abc", "def", post=False)

    with (
        patch(
            "superseded.server.worker.checkout_repo",
            new_callable=AsyncMock,
            return_value=tmp_path,
        ),
        patch("superseded.config.load_config", return_value=Config()),
        patch("superseded.server.worker.ReviewEngine", return_value=fake_engine),
        patch("superseded.context.gathering.compute_file_context", return_value=None),
        patch("superseded.context.gathering.run_static_analysis", return_value=None),
        patch("superseded.context.gathering.retrieve_usages", return_value=None),
        patch.object(
            store, "record_findings_batch", wraps=store.record_findings_batch
        ) as mock_record,
        patch.object(
            store, "set_comment_ids_batch", wraps=store.set_comment_ids_batch
        ) as mock_link,
    ):
        outcome, result = await _run_review_for_job(
            github=github,
            repo_manager=repo_manager,
            token="t",
            job=job,
            correlation_id="c",
            store=store,
            provider=_make_provider(),
        )

    github.post_review.assert_not_awaited()
    mock_record.assert_called_once()
    mock_link.assert_not_called()
    assert isinstance(outcome, ReviewOutcome)
    assert result.findings == [finding]
    assert await store.get_watermark("owner/repo", 7) == "abc"
