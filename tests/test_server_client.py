from __future__ import annotations

from unittest.mock import patch

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
            server_url="https://srv",
            server_key="sk",
            owner="o",
            repo="r",
            pr_number=7,
            client=_client_with(handler),
        )
    assert exc.value.exit_code == 2


@pytest.mark.parametrize("code", [429, 502, 500])
def test_submit_review_non_fatal_codes_exit_1(code):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(code, json={"detail": "bad"})

    with pytest.raises(ServerReviewError) as exc:
        submit_review(
            server_url="https://srv",
            server_key="sk",
            owner="o",
            repo="r",
            pr_number=7,
            client=_client_with(handler),
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
                            pass_name="security",
                            severity="critical",
                            file="a.py",
                            line=1,
                            title="t",
                            description="d",
                            suggestion="s",
                        )
                    ]
                ).model_dump(mode="json"),
                "error": None,
            },
        )

    result = poll_review(
        server_url="https://srv",
        server_key="sk",
        job_id="abc",
        budget=10.0,
        interval=0.0,
        client=_client_with(handler),
    )
    assert isinstance(result, ReviewResult)
    assert result.findings[0].file == "a.py"


def test_poll_review_failed_status_exit_1():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "failed", "result": None, "error": "boom"})

    with pytest.raises(ServerReviewError) as exc:
        poll_review(
            server_url="https://srv",
            server_key="sk",
            job_id="abc",
            budget=10.0,
            interval=0.0,
            client=_client_with(handler),
        )
    assert exc.value.exit_code == 1
    assert "boom" in str(exc.value)


def test_poll_review_unknown_job_404_exit_1():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Unknown or evicted job_id."})

    with pytest.raises(ServerReviewError) as exc:
        poll_review(
            server_url="https://srv",
            server_key="sk",
            job_id="abc",
            budget=10.0,
            interval=0.0,
            client=_client_with(handler),
        )
    assert exc.value.exit_code == 1


def test_poll_review_poll_401_exit_2():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Unauthorized"})

    with pytest.raises(ServerReviewError) as exc:
        poll_review(
            server_url="https://srv",
            server_key="sk",
            job_id="abc",
            budget=10.0,
            interval=0.0,
            client=_client_with(handler),
        )
    assert exc.value.exit_code == 2


def test_poll_review_timeout_exit_1():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "running", "result": None, "error": None})

    with pytest.raises(ServerReviewError) as exc:
        poll_review(
            server_url="https://srv",
            server_key="sk",
            job_id="abc",
            budget=0.0,
            interval=0.0,
            client=_client_with(handler),
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
        server_url="https://srv",
        server_key="sk",
        owner="o",
        repo="r",
        pr_number=7,
        poll_budget=10.0,
        poll_interval=0.0,
        client=_client_with(handler),
    )
    assert state["submitted"] is True
    assert isinstance(result, ReviewResult)


def test_submit_review_plain_text_501_exit_2():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(501, text="API key not configured on this server.")

    with pytest.raises(ServerReviewError) as exc:
        submit_review(
            server_url="https://srv",
            server_key="sk",
            owner="o",
            repo="r",
            pr_number=7,
            client=_client_with(handler),
        )
    assert exc.value.exit_code == 2
    assert "API key not configured" in str(exc.value)


def test_submit_review_200_without_job_id_exit_1():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "enqueued"})

    with pytest.raises(ServerReviewError) as exc:
        submit_review(
            server_url="https://srv",
            server_key="sk",
            owner="o",
            repo="r",
            pr_number=7,
            client=_client_with(handler),
        )
    assert exc.value.exit_code == 1
    assert "no job_id" in str(exc.value)


def test_submit_review_network_error_exit_1():
    with (
        patch.object(httpx.Client, "post", side_effect=httpx.ConnectError("boom")),
        pytest.raises(ServerReviewError) as exc,
    ):
        submit_review(
            server_url="https://srv",
            server_key="sk",
            owner="o",
            repo="r",
            pr_number=7,
        )
    assert "Failed to reach server" in str(exc.value)
    assert exc.value.exit_code == 1


def test_poll_review_get_url_contains_job_id():
    seen = {"path": None}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"status": "running", "result": None, "error": None})

    with pytest.raises(ServerReviewError) as exc:
        poll_review(
            server_url="https://srv",
            server_key="sk",
            job_id="abc",
            budget=0.0,
            interval=0.0,
            client=_client_with(handler),
        )
    assert seen["path"] == "/review/jobs/abc"
    assert exc.value.exit_code == 1
