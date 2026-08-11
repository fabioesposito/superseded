# CLI Server-Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `superseded review` run a review against a running review server (instead of locally): submit a job to `POST /review/pr`, poll a new `GET /review/jobs/{job_id}` until completion, and render findings locally — no provider API key on the client.

**Architecture:** An in-memory `JobStatus` registry on `ReviewWorker` tracks each job (`queued` → `running` → `completed`/`failed`) and stores the final `ReviewResult`. A new `GET /review/jobs/{job_id}` endpoint exposes that state. A new sync HTTP client module (`superseded/server/client.py`) handles submit + poll using `httpx`. The `review` CLI command gains `--server`/`--server-key`/`--owner`/`--repo`/`--no-post` flags and dispatches to the remote path when a server URL resolves (env > flag > config, matching the existing `resolve_provider`/`resolve_model` pattern).

**Tech Stack:** Python 3.14+, click, pydantic v2, FastAPI (server), `httpx` (existing direct dep), pytest (`asyncio_mode = "auto"`), ruff (`E,W,F,I,N,UP,B,SIM,TCH,RUF`). Run everything via `uv run …`. All external network is mocked in tests.

**Spec:** `docs/superseded/specs/2026-08-11-cli-server-mode-design.md`

---

## File Structure

**Create:**
- `src/superseded/server/client.py` — sync `httpx`-based submit/poll client used by the CLI. Single responsibility: talk to the review server over HTTP and return a `ReviewResult` (or raise `ServerReviewError`).
- `tests/test_server_jobs_endpoint.py` — tests for `GET /review/jobs/{job_id}`.
- `tests/test_cli_server_mode.py` — tests for the CLI client flow, flag handling, and dispatch.

**Modify:**
- `src/superseded/config.py` — add `server`, `server_key` fields to `Config`.
- `src/superseded/server/worker.py` — `JobStatus` dataclass, `self._jobs` registry + `_record_job` helper, `ReviewJob.post` field, `_run_review_for_job` returns `(ReviewOutcome, ReviewResult)` and honors `job.post`, `_process`/`_run_task`/`enqueue` record lifecycle.
- `src/superseded/server/app.py` — `GET /review/jobs/{job_id}` endpoint; `/review/pr` reads optional `post` body field.
- `src/superseded/cli.py` — `--server`/`--server-key`/`--owner`/`--repo`/`--no-post` flags; `SERVER_URL_ENV`/`SERVER_KEY_ENV` constants; `resolve_server`/`resolve_server_key`/`_run_review_remote`; dispatch; `init` probe.
- `tests/test_server_worker.py` — update tests that patch `_run_review_for_job` for the new `(ReviewOutcome, ReviewResult)` return; add registry + `post=False` tests.
- `tests/test_server_app.py` — add `/review/pr` `post` field test.

---

## Task 1: Config fields for server / server_key

**Files:**
- Modify: `src/superseded/config.py:20` (the `Config` class body)
- Test: `tests/test_config.py` (create if absent, else append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py` (create the file with this content if it does not exist; start with `from __future__ import annotations` to match house style):

```python
from __future__ import annotations

from superseded.config import Config


def test_config_defaults_server_none():
    cfg = Config()
    assert cfg.server is None
    assert cfg.server_key is None


def test_config_roundtrips_server_fields():
    cfg = Config(server="https://rev.example.com", server_key="sk-abc")
    assert cfg.server == "https://rev.example.com"
    assert cfg.server_key == "sk-abc"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'server'` (pydantic rejects unknown fields at construction).

- [ ] **Step 3: Add the fields**

In `src/superseded/config.py`, inside the `Config` class body, after the `verify: bool = True` line (line 39), add:

```python
    server: str | None = None
    server_key: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Lint + format**

Run: `uv run ruff check src/superseded/config.py tests/test_config.py && uv run ruff format src/superseded/config.py tests/test_config.py`

- [ ] **Step 6: Commit**

```bash
git add src/superseded/config.py tests/test_config.py
git commit -m "feat(config): add server and server_key fields"
```

---

## Task 2: Worker JobStatus registry + `_record_job` helper

This task adds the data structure and helper in isolation (no callers yet — wiring comes in Task 4).

**Files:**
- Modify: `src/superseded/server/worker.py`
- Test: `tests/test_server_worker.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_server_worker.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_server_worker.py::test_record_job_creates_status_queued -v`
Expected: FAIL with `AttributeError: 'ReviewWorker' object has no attribute '_record_job'`.

- [ ] **Step 3: Add `JobStatus`, registry, and helpers**

In `src/superseded/server/worker.py`:

(a) Add `import time` near the top imports (after `import logging`).

(b) After the existing `ReviewOutcome` dataclass (around line 50), add:

```python
@dataclass
class JobStatus:
    job_id: str
    status: str  # "queued" | "running" | "completed" | "failed"
    result: ReviewResult | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None


JOB_REGISTRY_CAP = 1000
```

(c) In `ReviewWorker.__init__`, after the `self._provider = provider` line, add:

```python
        self._jobs: dict[str, JobStatus] = {}
        self._job_cap = JOB_REGISTRY_CAP
```

(d) Add two methods to `ReviewWorker` (place them right after the `active_count` property, before `enqueue`):

```python
    def _record_job(
        self,
        job_id: str,
        status: str,
        result: ReviewResult | None = None,
        error: str | None = None,
    ) -> None:
        """Insert or update a job's status under the lock; evict when over cap."""
        async with self._lock:
            existing = self._jobs.get(job_id)
            created_at = existing.created_at if existing else time.time()
            completed_at = (
                time.time() if status in ("completed", "failed") else (existing.completed_at if existing else None)
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
        async with self._lock:
            return self._jobs.get(job_id)
```

Note: `_record_job` and `get_job_status` use `async with self._lock`, so they must be `await`ed from async code and called normally from sync test code via the pattern below.

(e) **Important correction:** the two helpers above must be sync (tests call them directly without `await`). Remove the `async` keyword from both — replace `async with self._lock:` with a plain critical section. Since the registry methods are also called from async worker code where `self._lock` is an `asyncio.Lock`, a sync method cannot acquire it. Use a `threading.Lock`-free approach instead: the registry is only mutated from the single worker event loop (asyncio is single-threaded per loop), and tests run their own loop. So drop the lock for registry mutations entirely and document that access is single-threaded within the event loop.

Revised final form of the two helpers (sync, no lock):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server_worker.py -k "record_job" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full worker file to confirm no regressions**

Run: `uv run pytest tests/test_server_worker.py -v`
Expected: PASS (all pre-existing tests still green; `_record_job` has no callers yet).

- [ ] **Step 6: Lint + format**

Run: `uv run ruff check src/superseded/server/worker.py tests/test_server_worker.py && uv run ruff format src/superseded/server/worker.py tests/test_server_worker.py`

- [ ] **Step 7: Commit**

```bash
git add src/superseded/server/worker.py tests/test_server_worker.py
git commit -m "feat(worker): add in-memory JobStatus registry and _record_job helper"
```

---

## Task 3: `_run_review_for_job` returns `(ReviewOutcome, ReviewResult)` + honors `ReviewJob.post`

**Files:**
- Modify: `src/superseded/server/worker.py` (`ReviewJob`, `_process`, `_run_review_for_job`)
- Test: `tests/test_server_worker.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server_worker.py`:

```python
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
        patch("superseded.server.worker._load_safe_config", new_callable=AsyncMock, return_value=Config()),
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
        patch("superseded.server.worker._load_safe_config", new_callable=AsyncMock, return_value=Config()),
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server_worker.py::test_run_review_for_job_post_false_skips_posting -v`
Expected: FAIL — `ReviewJob` has no `post` field, and `post_review` is called regardless.

- [ ] **Step 3: Add `post: bool = True` to `ReviewJob`**

In `src/superseded/server/worker.py`, in the `ReviewJob` dataclass, add after `passes`:

```python
    passes: list[str] | None = None
    post: bool = True
```

- [ ] **Step 4: Change `_run_review_for_job` return type and posting logic**

(a) Change the return annotation from `-> ReviewOutcome:` to:

```python
) -> tuple[ReviewOutcome, ReviewResult]:
```

(b) The progressive noop early-return (currently returns a bare `ReviewOutcome`). Replace that `return ReviewOutcome(...)` with:

```python
                    return (
                        ReviewOutcome(
                            conclusion="success",
                            title="No new commits since last review",
                            summary=f"Head {job.head_sha[:7]} unchanged since last review.",
                        ),
                        ReviewResult(),
                    )
```

(c) Wrap the posting + payload block so it runs only when `job.post` is `True`. Find the block that currently reads (around line 473):

```python
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
```

Replace with:

```python
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
```

(d) The `conclusion` line further down currently reads:

```python
        conclusion = "success" if payload["event"] != "REQUEST_CHANGES" else "failure"
```

It references `payload`, which no longer exists when `job.post` is `False`. Replace with:

```python
        conclusion = "success" if event != "REQUEST_CHANGES" else "failure"
```

(`event` defaults to `"COMMENT"` when posting was skipped, so the conclusion for a non-posting review is `"success"` — correct, since no changes were requested and the conclusion is only consumed by the check-run update, which is itself skipped when `post=False`.)

(e) Change the final `return ReviewOutcome(conclusion=conclusion, title=title, summary=summary)` to:

```python
        return ReviewOutcome(conclusion=conclusion, title=title, summary=summary), result
```

- [ ] **Step 5: Update existing tests that patch `_run_review_for_job`**

In `tests/test_server_worker.py`:

(a) `test_worker_processes_job` (line ~89): the patch is `new_callable=AsyncMock` with no `return_value`. Update to return the new tuple shape:

```python
    outcome_tuple = (ReviewOutcome(conclusion="success", title="0 findings", summary="ok"), ReviewResult())
    with patch(
        "superseded.server.worker._run_review_for_job",
        new_callable=AsyncMock,
        return_value=outcome_tuple,
    ) as mock_review:
        await worker._process(job)
```

(b) `test_worker_success_updates_existing_check_run` (line ~159): change `return_value=outcome` to `return_value=(outcome, ReviewResult())`:

```python
    outcome = ReviewOutcome(conclusion="success", title="0 finding(s)", summary="done")
    with patch(
        "superseded.server.worker._run_review_for_job",
        new_callable=AsyncMock,
        return_value=(outcome, ReviewResult()),
    ):
        await worker._process(job)
```

(c) `test_run_review_for_job_passes_context` (line ~177) and `test_run_review_for_job_forwards_conventions_and_specs` (line ~228) both call `await _run_review_for_job(...)` and **ignore the return value** (they only assert on `mock_engine.review.call_args.kwargs`). The return-type change from `ReviewOutcome` to `tuple[ReviewOutcome, ReviewResult]` therefore does not affect them — **no change needed**. (Verified by reading lines 219–278: both tests discard the await result.)

(d) Search the whole file for any other `_run_review_for_job` patch sites with `uv run pytest tests/test_server_worker.py -v` after the next step and fix any remaining failures by giving them `return_value=(ReviewOutcome(...), ReviewResult())`.

- [ ] **Step 6: Run the full worker test file**

Run: `uv run pytest tests/test_server_worker.py -v`
Expected: PASS (all tests, including the two new ones and the updated existing ones).

- [ ] **Step 7: Lint + format**

Run: `uv run ruff check src/superseded/server/worker.py tests/test_server_worker.py && uv run ruff format src/superseded/server/worker.py tests/test_server_worker.py`

- [ ] **Step 8: Commit**

```bash
git add src/superseded/server/worker.py tests/test_server_worker.py
git commit -m "feat(worker): _run_review_for_job returns tuple; ReviewJob.post gates PR posting"
```

---

## Task 4: Wire the registry into `enqueue` / `_run_task` / `_process`

**Files:**
- Modify: `src/superseded/server/worker.py` (`enqueue`, `_run_task`, `_process`)
- Test: `tests/test_server_worker.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server_worker.py`:

```python
@pytest.mark.asyncio
async def test_enqueue_records_queued_status():
    worker = ReviewWorker(
        github=FakeGitHubApp(), repo_manager=FakeRepoManager(), provider=_make_provider()
    )
    job = ReviewJob(
        installation_id=123, owner="o", repo="r", pr_number=1, head_sha="a", base_sha="b"
    )
    await worker.enqueue(job)
    status = worker.get_job_status(job.job_id)
    assert status is not None
    assert status.status == "queued"


@pytest.mark.asyncio
async def test_process_records_completed_with_result():
    worker = ReviewWorker(
        github=FakeGitHubApp(), repo_manager=FakeRepoManager(), provider=_make_provider()
    )
    job = ReviewJob(
        installation_id=123, owner="o", repo="r", pr_number=1, head_sha="a", base_sha="b"
    )
    fake_result = ReviewResult(
        findings=[
            Finding(
                pass_name="style", severity="nit", file="f.py", line=1,
                title="t", description="d", suggestion="s",
            )
        ]
    )
    with patch(
        "superseded.server.worker._run_review_for_job",
        new_callable=AsyncMock,
        return_value=(ReviewOutcome("success", "ok", "sum"), fake_result),
    ):
        await worker._process(job)

    status = worker.get_job_status(job.job_id)
    assert status.status == "completed"
    assert status.result is fake_result
    assert status.error is None
    assert status.completed_at is not None


@pytest.mark.asyncio
async def test_process_records_failed_on_review_exception():
    worker = ReviewWorker(
        github=FakeGitHubApp(), repo_manager=FakeRepoManager(), provider=_make_provider()
    )
    job = ReviewJob(
        installation_id=123, owner="o", repo="r", pr_number=1, head_sha="a", base_sha="b"
    )
    with patch(
        "superseded.server.worker._run_review_for_job",
        new_callable=AsyncMock,
        side_effect=RuntimeError("boom"),
    ):
        await worker._process(job)

    status = worker.get_job_status(job.job_id)
    assert status.status == "failed"
    assert status.error is not None
    assert "boom" in status.error
    assert status.completed_at is not None


@pytest.mark.asyncio
async def test_process_post_false_skips_check_run():
    worker = ReviewWorker(
        github=FakeGitHubApp(), repo_manager=FakeRepoManager(), provider=_make_provider()
    )
    job = ReviewJob(
        installation_id=123,
        owner="o",
        repo="r",
        pr_number=1,
        head_sha="a",
        base_sha="b",
        post=False,
    )
    with patch(
        "superseded.server.worker._run_review_for_job",
        new_callable=AsyncMock,
        return_value=(ReviewOutcome("success", "ok", "sum"), ReviewResult()),
    ):
        await worker._process(job)

    worker.github.create_check_run.assert_not_called()
    worker.github.update_check_run.assert_not_called()
    status = worker.get_job_status(job.job_id)
    assert status.status == "completed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server_worker.py::test_process_records_completed_with_result -v`
Expected: FAIL — `get_job_status` returns `None` (`_process` doesn't record yet).

- [ ] **Step 3: Record "queued" in `enqueue`**

In `src/superseded/server/worker.py`, in `enqueue`, add the record call before `self.queue.put_nowait(job)`:

```python
    async def enqueue(self, job: ReviewJob) -> None:
        self._record_job(job.job_id, "queued")
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
```

- [ ] **Step 4: Record "running" / terminal in `_run_task`**

Replace `_run_task` with:

```python
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
```

(`_process` records `completed`/`failed` itself — see Step 5. The `CancelledledError` branch records the cancelled case, since `_process` re-raises it.)

- [ ] **Step 5: Record terminal status inside `_process`**

`_process` currently returns `None` on every path. Replace the whole method with a version that records status at each terminal point and honors `job.post`:

```python
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
```

- [ ] **Step 6: Update the pre-existing `_process` tests for the no-check-run path is already covered by `test_process_post_false_skips_check_run`. Confirm the two original `_process` failure/success tests still patch `_run_review_for_job` with the tuple shape (Task 3 Step 5 already updated them).**

- [ ] **Step 7: Run the full worker test file**

Run: `uv run pytest tests/test_server_worker.py -v`
Expected: PASS (all tests).

- [ ] **Step 8: Run the full server app test file to confirm `_process` changes don't break endpoint tests**

Run: `uv run pytest tests/test_server_app.py -v`
Expected: PASS.

- [ ] **Step 9: Lint + format**

Run: `uv run ruff check src/superseded/server/worker.py tests/test_server_worker.py && uv run ruff format src/superseded/server/worker.py tests/test_server_worker.py`

- [ ] **Step 10: Commit**

```bash
git add src/superseded/server/worker.py tests/test_server_worker.py
git commit -m "feat(worker): record job lifecycle in registry; _process honors job.post"
```

---

## Task 5: `GET /review/jobs/{job_id}` endpoint

**Files:**
- Modify: `src/superseded/server/app.py`
- Test: `tests/test_server_jobs_endpoint.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_server_jobs_endpoint.py`:

```python
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from superseded.memory.store import MemoryStore
from superseded.models import Finding, ReviewResult
from superseded.server.app import create_app
from superseded.server.config import ServerConfig
from superseded.server.github import GitHubApp
from superseded.server.repo_manager import RepoManager
from superseded.server.worker import ReviewWorker


@pytest.fixture
def server(tmp_path):
    key_file = tmp_path / "key.pem"
    key_file.write_text("fake-key")
    config = ServerConfig(
        app_id=12345,
        webhook_secret="whsec_test",
        private_key_path=key_file,
        temp_dir=tmp_path / "repos",
        api_key="test-api-key",
    )
    github = GitHubApp(
        app_id=config.app_id,
        private_key_path=config.private_key_path,
        webhook_secret=config.webhook_secret,
    )
    repo_manager = RepoManager(base_path=config.temp_dir)
    worker = ReviewWorker(
        github=github, repo_manager=repo_manager, max_concurrent=1, provider=MagicMock()
    )
    store = MemoryStore(tmp_path / "memory.db")
    application = create_app(
        config=config, github=github, worker=worker, repo_manager=repo_manager, store=store
    )
    return SimpleNamespace(app=application, worker=worker, config=config)


@pytest.fixture
def client(server):
    return TestClient(server.app)


def _auth_headers():
    return {"Authorization": "Bearer test-api-key"}


def test_jobs_endpoint_unknown_job_404(client):
    r = client.get("/review/jobs/missing", headers=_auth_headers())
    assert r.status_code == 404


def test_jobs_endpoint_requires_auth(client):
    r = client.get("/review/jobs/anything")
    assert r.status_code == 401


def test_jobs_endpoint_501_when_no_api_key(tmp_path):
    key_file = tmp_path / "key.pem"
    key_file.write_text("fake-key")
    config = ServerConfig(
        app_id=1, webhook_secret="x", private_key_path=key_file,
        temp_dir=tmp_path / "r", api_key="",
    )
    github = GitHubApp(app_id=1, private_key_path=key_file, webhook_secret="x")
    repo_manager = RepoManager(base_path=config.temp_dir)
    worker = ReviewWorker(github=github, repo_manager=repo_manager, provider=MagicMock())
    store = MemoryStore(tmp_path / "m.db")
    app = create_app(config=config, github=github, worker=worker, repo_manager=repo_manager, store=store)
    r = TestClient(app).get("/review/jobs/x", headers={"Authorization": "Bearer anything"})
    assert r.status_code == 501


def test_jobs_endpoint_returns_queued(server, client):
    server.worker._record_job("job-1", "queued")
    r = client.get("/review/jobs/job-1", headers=_auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "queued"
    assert "result" not in data or data["result"] is None
    assert "error" not in data or data["error"] is None


def test_jobs_endpoint_returns_completed_with_result(server, client):
    result = ReviewResult(
        findings=[
            Finding(
                pass_name="security", severity="critical", file="a.py", line=3,
                title="T", description="D", suggestion="S",
            )
        ],
        warnings=["pass skipped"],
    )
    server.worker._record_job("job-2", "completed", result=result)
    r = client.get("/review/jobs/job-2", headers=_auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "completed"
    assert data["result"]["findings"][0]["file"] == "a.py"
    assert data["result"]["warnings"] == ["pass skipped"]
    assert data["error"] is None


def test_jobs_endpoint_returns_failed_with_error(server, client):
    server.worker._record_job("job-3", "failed", error="boom")
    r = client.get("/review/jobs/job-3", headers=_auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "failed"
    assert data["error"] == "boom"
    assert data["result"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server_jobs_endpoint.py -v`
Expected: FAIL with 404 from FastAPI (route not registered) or similar — the endpoint does not exist yet.

- [ ] **Step 3: Add the endpoint**

In `src/superseded/server/app.py`, add a new route inside `create_app`, after the `/review/pr` handler (after its `return {"status": "enqueued", "job_id": job.job_id}` and before the `/webhook` handler):

```python
    @app.get("/review/jobs/{job_id}")
    async def get_job_status(job_id: str, request: Request) -> Response:
        if not config.api_key:
            return Response(status_code=501, content="API key not configured on this server.")
        auth = request.headers.get("Authorization", "")
        expected = f"Bearer {config.api_key}"
        if not hmac.compare_digest(auth, expected):
            raise HTTPException(status_code=401, detail="Unauthorized")

        status = worker.get_job_status(job_id)
        if status is None:
            raise HTTPException(status_code=404, detail="Unknown or evicted job_id.")

        payload: dict = {"status": status.status, "result": None, "error": status.error}
        if status.status == "completed" and status.result is not None:
            payload["result"] = status.result.model_dump(mode="json")
        return payload
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server_jobs_endpoint.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Lint + format**

Run: `uv run ruff check src/superseded/server/app.py tests/test_server_jobs_endpoint.py && uv run ruff format src/superseded/server/app.py tests/test_server_jobs_endpoint.py`

- [ ] **Step 6: Commit**

```bash
git add src/superseded/server/app.py tests/test_server_jobs_endpoint.py
git commit -m "feat(server): add GET /review/jobs/{job_id} status endpoint"
```

---

## Task 6: `/review/pr` reads optional `post` body field

**Files:**
- Modify: `src/superseded/server/app.py` (the `/review/pr` handler)
- Test: `tests/test_server_app.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_server_app.py`. This test needs a `server` fixture with `api_key` set; the existing `server` fixture at the top of the file does **not** set `api_key`. Add a fixture that does, and a helper that mocks `github.resolve_installation` + `store.get_installation` so the auth/installation gates pass, then asserts the enqueued job carries `post=False`.

```python
@pytest.fixture
def server_with_api_key(tmp_path):
    key_file = tmp_path / "key.pem"
    key_file.write_text("fake-key")
    config = ServerConfig(
        app_id=12345,
        webhook_secret="whsec_test",
        private_key_path=key_file,
        temp_dir=tmp_path / "repos",
        api_key="test-api-key",
    )
    github = GitHubApp(
        app_id=config.app_id,
        private_key_path=config.private_key_path,
        webhook_secret=config.webhook_secret,
    )
    repo_manager = RepoManager(base_path=config.temp_dir)
    worker = ReviewWorker(
        github=github, repo_manager=repo_manager, max_concurrent=1, provider=MagicMock()
    )
    from superseded.memory.store import MemoryStore

    store = MemoryStore(tmp_path / "memory.db")
    application = create_app(
        config=config, github=github, worker=worker, repo_manager=repo_manager, store=store
    )
    return SimpleNamespace(
        app=application, store=store, worker=worker, github=github, config=config
    )


def test_review_pr_post_false_propagates_to_job(server_with_api_key):
    import asyncio

    client = TestClient(server_with_api_key.app)
    server_with_api_key.github.resolve_installation = MagicMock(return_value=99)
    server_with_api_key.github.fetch_pr_info = MagicMock(
        return_value={"head_sha": "abc", "base_sha": "def"}
    )
    server_with_api_key.store.record_installation(
        installation_id=99, owner="octocat", repos=["hello-world"]
    )

    captured: dict = {}

    async def fake_enqueue(job):
        captured["job"] = job

    server_with_api_key.worker.enqueue = fake_enqueue  # type: ignore[assignment]

    r = client.post(
        "/review/pr",
        headers={"Authorization": "Bearer test-api-key"},
        json={"owner": "octocat", "repo": "hello-world", "pr_number": 7, "post": False},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "enqueued"
    assert captured["job"].post is False


def test_review_pr_post_defaults_true_when_absent(server_with_api_key):
    client = TestClient(server_with_api_key.app)
    server_with_api_key.github.resolve_installation = MagicMock(return_value=99)
    server_with_api_key.github.fetch_pr_info = MagicMock(
        return_value={"head_sha": "abc", "base_sha": "def"}
    )
    server_with_api_key.store.record_installation(
        installation_id=99, owner="octocat", repos=["hello-world"]
    )

    captured: dict = {}

    async def fake_enqueue(job):
        captured["job"] = job

    server_with_api_key.worker.enqueue = fake_enqueue  # type: ignore[assignment]

    r = client.post(
        "/review/pr",
        headers={"Authorization": "Bearer test-api-key"},
        json={"owner": "octocat", "repo": "hello-world", "pr_number": 7},
    )
    assert r.status_code == 200
    assert captured["job"].post is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server_app.py::test_review_pr_post_false_propagates_to_job -v`
Expected: FAIL — `ReviewJob` constructed in `/review/pr` doesn't read the `post` field, so `captured["job"].post` is the default `True` (test 1 fails); test 2 passes already.

- [ ] **Step 3: Read the `post` field in the `/review/pr` handler**

In `src/superseded/server/app.py`, inside the `review_pr` handler, after the `passes_list` parsing block and before `installation_id = await github.resolve_installation(...)`, add parsing for `post`:

```python
        post_field = body.get("post", True)
        if not isinstance(post_field, bool):
            raise HTTPException(
                status_code=422, detail="'post' must be a boolean if present."
            )
```

Then update the `ReviewJob(...)` construction to include `post=post_field`:

```python
        job = ReviewJob(
            installation_id=installation_id,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            head_sha=pr_info["head_sha"],
            base_sha=pr_info["base_sha"],
            passes=passes_list,
            post=post_field,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server_app.py -v`
Expected: PASS (all, including the two new ones).

- [ ] **Step 5: Lint + format**

Run: `uv run ruff check src/superseded/server/app.py tests/test_server_app.py && uv run ruff format src/superseded/server/app.py tests/test_server_app.py`

- [ ] **Step 6: Commit**

```bash
git add src/superseded/server/app.py tests/test_server_app.py
git commit -m "feat(server): /review/pr reads optional 'post' body field (default true)"
```

---

## Task 7: CLI HTTP client module (`superseded/server/client.py`)

**Files:**
- Create: `src/superseded/server/client.py`
- Test: `tests/test_server_client.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_server_client.py`:

```python
from __future__ import annotations

import httpx
import pytest

from superseded.models import Finding, ReviewResult
from superseded.server.client import (
    ServerReviewError,
    poll_review,
    review_via_server,
    submit_review,
)


def _client_with(handler):
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport)


def test_submit_review_returns_job_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/review/pr"
        assert request.headers["Authorization"] == "Bearer sk"
        import json

        body = json.loads(request.content)
        assert body == {"owner": "o", "repo": "r", "pr_number": 7}
        return httpx.Response(200, json={"status": "enqueued", "job_id": "abc"})

    job_id = submit_review(
        server_url="https://srv",
        server_key="sk",
        owner="o",
        repo="r",
        pr_number=7,
        client=_client_with(handler),
    )
    assert job_id == "abc"


def test_submit_review_passes_post_false_and_passes():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"status": "enqueued", "job_id": "abc"})

    submit_review(
        server_url="https://srv",
        server_key="sk",
        owner="o",
        repo="r",
        pr_number=7,
        passes=["security", "style"],
        post=False,
        client=_client_with(handler),
    )
    assert captured["body"]["passes"] == "security,style"
    assert captured["body"]["post"] is False


@pytest.mark.parametrize("code", [401, 403, 409, 422, 501])
def test_submit_review_fatal_codes_exit_2(code):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(code, json={"detail": "bad"})

    with pytest.raises(ServerReviewError) as exc:
        submit_review(
            server_url="https://srv", server_key="sk",
            owner="o", repo="r", pr_number=7, client=_client_with(handler),
        )
    assert exc.value.exit_code == 2


@pytest.mark.parametrize("code", [429, 502, 500])
def test_submit_review_non_fatal_codes_exit_1(code):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(code, json={"detail": "bad"})

    with pytest.raises(ServerReviewError) as exc:
        submit_review(
            server_url="https://srv", server_key="sk",
            owner="o", repo="r", pr_number=7, client=_client_with(handler),
        )
    assert exc.value.exit_code == 1


def test_poll_review_returns_result():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] < 2:
            return httpx.Response(200, json={"status": "running", "result": None, "error": None})
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "result": ReviewResult(
                    findings=[
                        Finding(
                            pass_name="security", severity="critical", file="a.py", line=1,
                            title="t", description="d", suggestion="s",
                        )
                    ]
                ).model_dump(mode="json"),
                "error": None,
            },
        )

    result = poll_review(
        server_url="https://srv", server_key="sk", job_id="abc",
        budget=10.0, interval=0.0, client=_client_with(handler),
    )
    assert isinstance(result, ReviewResult)
    assert result.findings[0].file == "a.py"


def test_poll_review_failed_status_exit_1():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "failed", "result": None, "error": "boom"})

    with pytest.raises(ServerReviewError) as exc:
        poll_review(
            server_url="https://srv", server_key="sk", job_id="abc",
            budget=10.0, interval=0.0, client=_client_with(handler),
        )
    assert exc.value.exit_code == 1
    assert "boom" in str(exc.value)


def test_poll_review_unknown_job_404_exit_1():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Unknown or evicted job_id."})

    with pytest.raises(ServerReviewError) as exc:
        poll_review(
            server_url="https://srv", server_key="sk", job_id="abc",
            budget=10.0, interval=0.0, client=_client_with(handler),
        )
    assert exc.value.exit_code == 1


def test_poll_review_poll_401_exit_2():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Unauthorized"})

    with pytest.raises(ServerReviewError) as exc:
        poll_review(
            server_url="https://srv", server_key="sk", job_id="abc",
            budget=10.0, interval=0.0, client=_client_with(handler),
        )
    assert exc.value.exit_code == 2


def test_poll_review_timeout_exit_1():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "running", "result": None, "error": None})

    with pytest.raises(ServerReviewError) as exc:
        poll_review(
            server_url="https://srv", server_key="sk", job_id="abc",
            budget=0.0, interval=0.0, client=_client_with(handler),
        )
    assert "timed out" in str(exc.value).lower()
    assert exc.value.exit_code == 1


def test_review_via_server_orchestrates_submit_and_poll():
    state = {"submitted": False, "n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/review/pr":
            state["submitted"] = True
            return httpx.Response(200, json={"status": "enqueued", "job_id": "abc"})
        state["n"] += 1
        if state["n"] < 2:
            return httpx.Response(200, json={"status": "running", "result": None, "error": None})
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "result": ReviewResult().model_dump(mode="json"),
                "error": None,
            },
        )

    result = review_via_server(
        server_url="https://srv", server_key="sk",
        owner="o", repo="r", pr_number=7,
        poll_budget=10.0, poll_interval=0.0, client=_client_with(handler),
    )
    assert state["submitted"] is True
    assert isinstance(result, ReviewResult)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'superseded.server.client'`.

- [ ] **Step 3: Create the client module**

Create `src/superseded/server/client.py`:

```python
from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from superseded.models import ReviewResult

_SUBMIT_FATAL_CODES = {401, 403, 409, 422, 501}
_POLL_FATAL_CODES = {401}
DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_SUBMIT_TIMEOUT = 30.0
DEFAULT_POLL_TIMEOUT = 30.0


class ServerReviewError(Exception):
    """Terminal submit/poll failure carrying a CLI exit code."""

    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text or response.reason_phrase
    if isinstance(body, dict):
        return str(body.get("detail") or body)
    return str(body)


def submit_review(
    *,
    server_url: str,
    server_key: str,
    owner: str,
    repo: str,
    pr_number: int,
    passes: list[str] | None = None,
    post: bool = True,
    client: httpx.Client | None = None,
) -> str:
    """POST {server_url}/review/pr and return the job_id. Raises ServerReviewError."""
    own_client = client or httpx.Client(timeout=DEFAULT_SUBMIT_TIMEOUT)
    body: dict = {"owner": owner, "repo": repo, "pr_number": pr_number}
    if passes:
        body["passes"] = ",".join(passes)
    if not post:
        body["post"] = False
    try:
        response = own_client.post(
            f"{server_url.rstrip('/')}/review/pr",
            headers={
                "Authorization": f"Bearer {server_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=DEFAULT_SUBMIT_TIMEOUT,
        )
    except httpx.HTTPError as err:
        raise ServerReviewError(f"Failed to reach server: {err}", exit_code=1) from err
    if response.status_code == 200:
        data = response.json()
        job_id = data.get("job_id")
        if not job_id:
            raise ServerReviewError(
                f"Server returned 200 but no job_id: {data}", exit_code=1
            )
        return str(job_id)
    exit_code = 2 if response.status_code in _SUBMIT_FATAL_CODES else 1
    raise ServerReviewError(_detail(response), exit_code=exit_code)


def poll_review(
    *,
    server_url: str,
    server_key: str,
    job_id: str,
    budget: float,
    interval: float = DEFAULT_POLL_INTERVAL,
    client: httpx.Client | None = None,
) -> ReviewResult:
    """Poll GET {server_url}/review/jobs/{job_id} until terminal. Raises ServerReviewError."""
    own_client = client or httpx.Client(timeout=DEFAULT_POLL_TIMEOUT)
    url = f"{server_url.rstrip('/')}/review/jobs/{job_id}"
    headers = {"Authorization": f"Bearer {server_key}"}
    deadline = time.monotonic() + budget
    while True:
        try:
            response = own_client.get(url, headers=headers, timeout=DEFAULT_POLL_TIMEOUT)
        except httpx.HTTPError as err:
            raise ServerReviewError(f"Failed to reach server: {err}", exit_code=1) from err
        if response.status_code == 200:
            data = response.json()
            status = data.get("status")
            if status == "completed":
                result_data = data.get("result")
                if result_data is None:
                    return ReviewResult()
                return ReviewResult.model_validate(result_data)
            if status == "failed":
                raise ServerReviewError(
                    data.get("error") or "review failed", exit_code=1
                )
            if time.monotonic() >= deadline:
                raise ServerReviewError(
                    "review timed out (job did not complete within budget)", exit_code=1
                )
            time.sleep(max(0.0, interval))
            continue
        exit_code = 2 if response.status_code in _POLL_FATAL_CODES else 1
        raise ServerReviewError(_detail(response), exit_code=exit_code)


def review_via_server(
    *,
    server_url: str,
    server_key: str,
    owner: str,
    repo: str,
    pr_number: int,
    passes: list[str] | None = None,
    post: bool = True,
    poll_budget: float,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    on_status: Callable[[str], None] | None = None,
    client: httpx.Client | None = None,
) -> ReviewResult:
    """Submit a review job and poll until complete. Returns the ReviewResult."""
    own_client = client or httpx.Client(timeout=DEFAULT_SUBMIT_TIMEOUT)
    job_id = submit_review(
        server_url=server_url,
        server_key=server_key,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        passes=passes,
        post=post,
        client=own_client,
    )
    if on_status is not None:
        on_status(f"Review enqueued (job_id={job_id}). Polling…")
    return poll_review(
        server_url=server_url,
        server_key=server_key,
        job_id=job_id,
        budget=poll_budget,
        interval=poll_interval,
        client=own_client,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server_client.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Lint + format**

Run: `uv run ruff check src/superseded/server/client.py tests/test_server_client.py && uv run ruff format src/superseded/server/client.py tests/test_server_client.py`

- [ ] **Step 6: Commit**

```bash
git add src/superseded/server/client.py tests/test_server_client.py
git commit -m "feat(server): add sync httpx submit/poll client for CLI server-mode"
```

---

## Task 8: CLI flags + dispatch + `_run_review_remote`

**Files:**
- Modify: `src/superseded/cli.py`
- Test: `tests/test_cli_server_mode.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_server_mode.py`. These use `click.testing.CliRunner` and monkeypatch `review_via_server` so no real HTTP happens.

```python
from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from superseded.cli import cli
from superseded.models import Finding, ReviewResult


def _ok_result():
    return ReviewResult(
        findings=[
            Finding(
                pass_name="security", severity="critical", file="a.py", line=1,
                title="T", description="D", suggestion="S",
            )
        ]
    )


def test_review_server_mode_requires_pr():
    runner = CliRunner()
    with patch("superseded.cli.review_via_server") as mock_rev:
        result = runner.invoke(
            cli,
            ["review", "--server", "https://srv", "--server-key", "sk"],
        )
    assert result.exit_code == 2
    assert "--pr" in result.output
    mock_rev.assert_not_called()


def test_review_server_mode_rejects_diff_combo():
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["review", "--server", "https://srv", "--server-key", "sk",
         "--pr", "1", "--diff", "HEAD~1..HEAD"],
    )
    assert result.exit_code == 2
    assert "cannot be combined" in result.output or "diff" in result.output.lower()


def test_review_server_mode_renders_table(monkeypatch):
    monkeypatch.setenv("SUPERSEDED_SERVER_URL", "https://srv")
    monkeypatch.setenv("SUPERSEDED_SERVER_KEY", "sk")
    monkeypatch.setattr("superseded.cli.current_repo", lambda: "octocat/hello-world")
    runner = CliRunner()
    with patch("superseded.cli.review_via_server", return_value=_ok_result()) as mock_rev:
        result = runner.invoke(cli, ["review", "--pr", "7"])
    assert result.exit_code == 0
    assert "a.py" in result.output
    mock_rev.assert_called_once()
    kwargs = mock_rev.call_args.kwargs
    assert kwargs["server_url"] == "https://srv"
    assert kwargs["server_key"] == "sk"
    assert kwargs["owner"] == "octocat"
    assert kwargs["repo"] == "hello-world"
    assert kwargs["pr_number"] == 7


def test_review_server_mode_json():
    runner = CliRunner()
    with (
        patch("superseded.cli.current_repo", lambda: "octocat/hello-world"),
        patch("superseded.cli.review_via_server", return_value=_ok_result()),
    ):
        result = runner.invoke(
            cli,
            ["review", "--server", "https://srv", "--server-key", "sk",
             "--pr", "7", "--format", "json"],
        )
    assert result.exit_code == 0
    assert '"findings"' in result.output
    assert "a.py" in result.output


def test_review_server_mode_no_post_passes_post_false():
    runner = CliRunner()
    with (
        patch("superseded.cli.current_repo", lambda: "octocat/hello-world"),
        patch("superseded.cli.review_via_server", return_value=_ok_result()) as mock_rev,
    ):
        result = runner.invoke(
            cli,
            ["review", "--server", "https://srv", "--server-key", "sk",
             "--pr", "7", "--no-post"],
        )
    assert result.exit_code == 0
    assert mock_rev.call_args.kwargs["post"] is False


def test_review_server_mode_post_flag_warns():
    runner = CliRunner()
    with (
        patch("superseded.cli.current_repo", lambda: "octocat/hello-world"),
        patch("superseded.cli.review_via_server", return_value=_ok_result()),
    ):
        result = runner.invoke(
            cli,
            ["review", "--server", "https://srv", "--server-key", "sk",
             "--pr", "7", "--post"],
        )
    assert result.exit_code == 0
    assert "--post" in result.output or "post" in result.output.lower()


def test_review_server_mode_missing_key_exit_2(monkeypatch):
    monkeypatch.delenv("SUPERSEDED_SERVER_KEY", raising=False)
    runner = CliRunner()
    with patch("superseded.cli.current_repo", lambda: "octocat/hello-world"):
        result = runner.invoke(
            cli,
            ["review", "--server", "https://srv", "--pr", "7"],
        )
    assert result.exit_code == 2


def test_review_server_mode_no_remote_no_owner_exit_2():
    runner = CliRunner()
    with patch("superseded.cli.current_repo", lambda: None):
        result = runner.invoke(
            cli,
            ["review", "--server", "https://srv", "--server-key", "sk", "--pr", "7"],
        )
    assert result.exit_code == 2


def test_review_server_mode_server_error_exit_code(monkeypatch):
    from superseded.server.client import ServerReviewError

    monkeypatch.setenv("SUPERSEDED_SERVER_URL", "https://srv")
    monkeypatch.setenv("SUPERSEDED_SERVER_KEY", "sk")
    runner = CliRunner()
    with (
        patch("superseded.cli.current_repo", lambda: "octocat/hello-world"),
        patch(
            "superseded.cli.review_via_server",
            side_effect=ServerReviewError("nope", exit_code=2),
        ),
    ):
        result = runner.invoke(cli, ["review", "--pr", "7"])
    assert result.exit_code == 2


def test_review_server_mode_warnings_exit_3():
    result_with_warnings = ReviewResult(
        findings=[],
        warnings=["security pass skipped"],
    )
    runner = CliRunner()
    with (
        patch("superseded.cli.current_repo", lambda: "octocat/hello-world"),
        patch("superseded.cli.review_via_server", return_value=result_with_warnings),
    ):
        result = runner.invoke(
            cli,
            ["review", "--server", "https://srv", "--server-key", "sk", "--pr", "7"],
        )
    assert result.exit_code == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_server_mode.py -v`
Expected: FAIL — `--server` flag unknown; `review_via_server` import fails.

- [ ] **Step 3: Add constants, import, and resolve helpers**

In `src/superseded/cli.py`, near the other `*_ENV` constants (after `VERBOSE_ENV = "VERBOSE"`, line 55), add:

```python
SERVER_URL_ENV = "SUPERSEDED_SERVER_URL"
SERVER_KEY_ENV = "SUPERSEDED_SERVER_KEY"
```

Add a top-level import (alongside the other `from superseded.…` imports near the top). `superseded.server.__init__` is empty, so this import is cheap (only pulls httpx + models, no FastAPI/worker cascade) and makes `review_via_server` patchable as `superseded.cli.review_via_server`:

```python
from superseded.server.client import ServerReviewError, review_via_server
```

After the `resolve_verify` function (around line 151), add:

```python
def resolve_server(server_flag: str | None, config: Config) -> str | None:
    return os.environ.get(SERVER_URL_ENV) or server_flag or config.server


def resolve_server_key(key_flag: str | None, config: Config) -> str | None:
    return os.environ.get(SERVER_KEY_ENV) or key_flag or config.server_key
```

- [ ] **Step 4: Add the new flags to the `review` command**

In `src/superseded/cli.py`, in the decorator stack above `def review(...)`, add these options (place them after the existing `--post` option and before `@click.argument("files", ...)`):

```python
@click.option("--server", "server_url_flag", default=None, help="Review server URL (server-mode).")
@click.option("--server-key", "server_key_flag", default=None, help="Review server bearer key.")
@click.option("--owner", default=None, help="PR repo owner (defaults to current git remote).")
@click.option("--repo", "repo_name", default=None, help="PR repo name (defaults to current git remote).")
@click.option("--no-post", "no_post", is_flag=True, help="Suppress server-side PR posting (server-mode).")
```

Then add the matching parameters to the `def review(...)` signature (after `files: tuple[str, ...]` is fine, but params must match decorator order; place them before `files`):

```python
    server_url_flag: str | None,
    server_key_flag: str | None,
    owner: str | None,
    repo_name: str | None,
    no_post: bool,
```

- [ ] **Step 5: Add the server-mode dispatch at the top of `review`'s body**

At the very start of the `review(...)` function body (after the docstring, before `log_config = load_config(config_path)`), add:

```python
    log_config = load_config(config_path)
    setup_logging(
        resolve_log_format(ctx.obj.get("log_format") if ctx.obj else None, log_config),
        resolve_log_level(ctx.obj.get("log_level") if ctx.obj else None, log_config),
    )

    server_url = resolve_server(server_url_flag, log_config)
    if server_url:
        if files or diff_range or staged:
            click.echo(
                "Error: --server cannot be combined with --diff/--files/--staged.",
                err=True,
            )
            sys.exit(2)
        _run_review_remote(
            server_url=server_url,
            server_key=resolve_server_key(server_key_flag, log_config),
            pr=pr,
            owner_flag=owner,
            repo_flag=repo_name,
            post=not no_post,
            post_flag_set=post,
            output_format=output_format,
            passes=passes,
            timeout=timeout,
            config_path=config_path,
        )
        return
```

(The pre-existing `log_config = load_config(config_path)` / `setup_logging(...)` lines that were already at the top of `review` should not be duplicated — keep a single pair. The block above is the final form: load config, set up logging, then branch.)

- [ ] **Step 6: Implement `_run_review_remote`**

Add this function in `cli.py` after `_run_review` (place it right before the `@cli.command()` for `init`). `review_via_server` and `ServerReviewError` are already imported at module level (Step 3).

```python
def _run_review_remote(
    *,
    server_url: str,
    server_key: str | None,
    pr: int | None,
    owner_flag: str | None,
    repo_flag: str | None,
    post: bool,
    post_flag_set: bool,
    output_format: str | None,
    passes: str | None,
    timeout: int | None,
    config_path: Path | None,
) -> None:
    if pr is None:
        click.echo("Error: --server requires --pr.", err=True)
        sys.exit(2)
    if server_key is None:
        click.echo(
            "Error: server key required. Set --server-key, SUPERSEDED_SERVER_KEY, "
            "or 'server_key:' in .superseded.yaml.",
            err=True,
        )
        sys.exit(2)

    owner = owner_flag
    repo = repo_flag
    if owner is None or repo is None:
        remote = current_repo()
        if remote and "/" in remote:
            r_owner, _, r_name = remote.partition("/")
            owner = owner or r_owner
            repo = repo or r_name
    if not owner or not repo:
        click.echo(
            "Error: could not resolve owner/repo. Pass --owner and --repo.",
            err=True,
        )
        sys.exit(2)

    if post_flag_set:
        _status(
            "Warning: --post has no effect in server-mode; the server posts by "
            "default. Use --no-post to suppress."
        )

    config = load_config(config_path)
    fmt = output_format or config.format
    pass_list = _parse_passes(passes)
    poll_budget = float(timeout if timeout is not None else DEFAULT_TIMEOUT)

    try:
        result = review_via_server(
            server_url=server_url,
            server_key=server_key,
            owner=owner,
            repo=repo,
            pr_number=pr,
            passes=pass_list,
            post=post,
            poll_budget=poll_budget,
            on_status=_status,
        )
    except ServerReviewError as err:
        click.echo(f"Error: {err}", err=True)
        sys.exit(err.exit_code)

    if fmt == "json":
        click.echo(format_json(result))
    elif fmt == "markdown":
        click.echo(format_markdown(result))
    else:
        click.echo(format_table(result))

    for w in result.warnings:
        click.echo(f"\nWarning: {w}", err=True)

    if result.warnings:
        sys.exit(EXIT_PARTIAL_FAILURE)
```

- [ ] **Step 7: Run the new tests**

Run: `uv run pytest tests/test_cli_server_mode.py -v`
Expected: PASS (10 tests).

- [ ] **Step 8: Run the existing CLI test file to confirm no flag regressions**

Run: `uv run pytest tests/test_cli.py -v` (if this file exists; otherwise `uv run pytest tests/ -k cli -v`)
Expected: PASS.

- [ ] **Step 9: Lint + format**

Run: `uv run ruff check src/superseded/cli.py tests/test_cli_server_mode.py && uv run ruff format src/superseded/cli.py tests/test_cli_server_mode.py`

- [ ] **Step 10: Commit**

```bash
git add src/superseded/cli.py tests/test_cli_server_mode.py
git commit -m "feat(cli): add --server/--server-key/--owner/--repo/--no-post and remote dispatch"
```

---

## Task 9: `init` probes server env vars

**Files:**
- Modify: `src/superseded/cli.py` (`_run_init`)
- Test: `tests/test_init.py` (append, or create if absent)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_init.py` (create with `from __future__ import annotations` if it does not exist):

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from superseded.cli import cli


def test_init_reports_server_env_when_set(tmp_path, monkeypatch):
    target = tmp_path / "out.yaml"
    monkeypatch.setenv("SUPERSEDED_SERVER_URL", "https://rev.example.com")
    monkeypatch.setenv("SUPERSEDED_SERVER_KEY", "sk-test")
    runner = CliRunner()
    with patch("shutil.which", return_value=None):
        result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 0
    assert "SUPERSEDED_SERVER_URL" in result.output
    assert "https://rev.example.com" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_init.py::test_init_reports_server_env_when_set -v`
Expected: FAIL — the server URL is not echoed.

- [ ] **Step 3: Add the probe to `_run_init`**

In `src/superseded/cli.py`, inside `_run_init`, after the API-keys block (after the `else:` that warns about no keys, before `cfg = Config(provider="deepseek")`), add:

```python
    server_url = os.environ.get("SUPERSEDED_SERVER_URL")
    server_key = os.environ.get("SUPERSEDED_SERVER_KEY")
    if server_url:
        _status(f"Review server: {server_url} (SUPERSEDED_SERVER_URL)")
        if not server_key:
            _status("  SUPERSEDED_SERVER_KEY not set — server-mode will need --server-key.")
    elif server_key:
        _status("SUPERSEDED_SERVER_KEY set but SUPERSEDED_SERVER_URL is not — server-mode disabled.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_init.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + format**

Run: `uv run ruff check src/superseded/cli.py tests/test_init.py && uv run ruff format src/superseded/cli.py tests/test_init.py`

- [ ] **Step 6: Commit**

```bash
git add src/superseded/cli.py tests/test_init.py
git commit -m "feat(init): probe SUPERSEDED_SERVER_URL/SERVER_KEY and report status"
```

---

## Task 10: Docs — README, AGENTS.md, landing page

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `index.html`

- [ ] **Step 1: README — add a "Server-mode" subsection**

Read `README.md` to find the CLI usage section (the one documenting `uv run superseded review …`). Add a short subsection immediately after the local-usage example:

```markdown
### Server-mode (review via a running server)

Point the CLI at a running review server instead of calling the provider
locally. Only `--pr` is supported (the server fetches the diff via its GitHub
App). No provider API key is needed on the client — only the server URL and key.

```bash
SUPERSEDED_SERVER_URL=https://reviews.example.com \
SUPERSEDED_SERVER_KEY=... \
uv run superseded review --pr 123
```

Add `--no-post` to suppress the server's PR comments (silent preview; findings
still print locally). Env > flag > config (`.superseded.yaml` `server:` /
`server_key:`) for both values.
```

- [ ] **Step 2: AGENTS.md — extend the architecture note**

In `AGENTS.md`, under the "Architecture notes" section, find the bullet that begins "Entry point:" and add a new bullet after the CLI/`review`/`feedback` entry-point bullet:

```markdown
- Server-mode: `superseded review --server <url> --pr N` submits to the server's `POST /review/pr`, polls `GET /review/jobs/{job_id}` until terminal, and renders findings locally via the existing formatters. The HTTP client lives in `superseded/server/client.py` (`review_via_server`, `submit_review`, `poll_review`, `ServerReviewError`). `--no-post` sets `post:false` in the request body; the worker (`ReviewJob.post`) then skips check-run creation and review comments. `ReviewWorker._jobs` (an in-memory `JobStatus` registry, capped at 1000, evict oldest) is the source of truth for job status; it is wiped on server restart. Config precedence for `server`/`server_key` mirrors `provider`/`model`: env (`SUPERSEDED_SERVER_URL`/`SUPERSEDED_SERVER_KEY`) > flag (`--server`/`--server-key`) > config file.
```

- [ ] **Step 3: Landing page — add server-mode to the usage section**

Read `index.html` to find the install/usage section. Add a brief server-mode example alongside the existing CLI examples (match the existing markup exactly — do not invent new classes or sections):

```html
<!-- Server-mode: review via a running server -->
<pre><code>SUPERSEDED_SERVER_URL=https://reviews.example.com \
SUPERSEDED_SERVER_KEY=... \
superseded review --pr 123</code></pre>
```

(Place it next to the existing review example; keep the surrounding tags identical to the neighbor.)

- [ ] **Step 4: Verify docs build/links are not broken**

There is no docs build step. Just re-read each edited region and confirm formatting is consistent with neighbors.

- [ ] **Step 5: Commit**

```bash
git add README.md AGENTS.md index.html
git commit -m "docs: document CLI server-mode (review via a running server)"
```

---

## Final verification

- [ ] **Run the entire suite**

```bash
uv run pytest tests/ -v
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

Expected: all tests PASS; ruff clean; format check clean.

- [ ] **Smoke-test the CLI help shows new flags**

```bash
uv run superseded review --help
```

Expected: `--server`, `--server-key`, `--owner`, `--repo`, `--no-post` all appear with their help strings.
