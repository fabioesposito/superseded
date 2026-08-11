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


def _json_or_none(response: httpx.Response) -> dict | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


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
        data = _json_or_none(response)
        if not data or not data.get("job_id"):
            raise ServerReviewError(f"Server returned 200 but no job_id: {data}", exit_code=1)
        return str(data["job_id"])
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
            data = _json_or_none(response)
            if data is None:
                raise ServerReviewError("unexpected response from server", exit_code=1)
            status = data.get("status")
            if status == "completed":
                result_data = data.get("result")
                if result_data is None:
                    return ReviewResult()
                return ReviewResult.model_validate(result_data)
            if status == "failed":
                raise ServerReviewError(data.get("error") or "review failed", exit_code=1)
            if time.monotonic() >= deadline:
                raise ServerReviewError(
                    f"review timed out (job {job_id} did not complete within budget)",
                    exit_code=1,
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
