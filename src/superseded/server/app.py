from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response

if TYPE_CHECKING:
    from superseded.memory.backend import Store
    from superseded.server.config import ServerConfig
    from superseded.server.github import GitHubApp
    from superseded.server.repo_manager import RepoManager
    from superseded.server.worker import ReviewWorker

logger = logging.getLogger(__name__)

WEBHOOK_RATE_LIMIT = 60
WEBHOOK_RATE_WINDOW = 60.0
REPLAY_WINDOW = 300.0


def _is_repo_authorized(installation: dict | None, owner: str, repo: str) -> bool:
    """Return True if ``owner/repo`` is in the installation's recorded repos.

    ``installation["repos"]`` is a JSON-encoded list of either ``"owner/repo"``
    full names or bare ``"repo"`` names (as recorded by the installation event
    handler). An unknown/unrecorded installation (``None``) or malformed payload
    is treated as not authorized.
    """
    if not installation:
        return False
    try:
        authorized = json.loads(installation.get("repos", "[]"))
    except TypeError, ValueError:
        return False
    return f"{owner}/{repo}" in authorized or repo in authorized


class ReplayProtector:
    """Reject duplicate webhook payloads within a sliding time window."""

    def __init__(self, window: float = REPLAY_WINDOW) -> None:
        self._window = window
        self._recent: dict[str, float] = {}

    def is_replay(self, payload: bytes, signature: str) -> bool:
        now = time.time()
        cutoff = now - self._window
        self._recent = {k: v for k, v in self._recent.items() if v > cutoff}
        sig_hash = hashlib.sha256(signature.encode()).hexdigest()
        if sig_hash in self._recent:
            return True
        self._recent[sig_hash] = now
        return False


class RateLimiter:
    """Simple in-memory sliding-window rate limiter per client IP.

    NOTE: keyed on client IP only. When running behind a load balancer or
    reverse proxy, all requests share one IP and the limiter becomes global.
    For routed deployments, consider trusting ``X-Forwarded-For`` or limiting
    per verified installation instead.
    """

    def __init__(self, max_requests: int, window: float) -> None:
        self.max_requests = max_requests
        self.window = window
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        hits = self._hits.setdefault(key, [])
        cutoff = now - self.window
        while hits and hits[0] < cutoff:
            hits.pop(0)
        if len(hits) >= self.max_requests:
            return False
        hits.append(now)
        return True


def create_app(
    config: ServerConfig,
    github: GitHubApp,
    worker: ReviewWorker,
    repo_manager: RepoManager,
    store: Store,
    lifespan: Callable[[FastAPI], AsyncIterator[None]] | None = None,
) -> FastAPI:
    app = FastAPI(title="Superseded", version="0.1.0", lifespan=lifespan)
    start_time = time.time()
    rate_limiter = RateLimiter(WEBHOOK_RATE_LIMIT, WEBHOOK_RATE_WINDOW)
    replay_limiter = ReplayProtector()

    @app.get("/health")
    async def health(request: Request) -> dict:
        token = config.health_token
        if token:
            auth = request.headers.get("Authorization", "")
            expected = f"Bearer {token}"
            if not hmac.compare_digest(auth, expected):
                raise HTTPException(status_code=401, detail="Unauthorized")
            return {
                "status": "ok",
                "queue_depth": worker.queue.qsize(),
                "active_reviews": worker.active_count,
                "disk_usage": repo_manager.disk_usage(),
                "uptime_seconds": time.time() - start_time,
            }
        return {"status": "ok"}

    @app.post("/review")
    async def manual_review(request: Request) -> Response:
        if not config.api_key:
            return Response(status_code=501, content="API key not configured on this server.")
        auth = request.headers.get("Authorization", "")
        expected = f"Bearer {config.api_key}"
        if not hmac.compare_digest(auth, expected):
            raise HTTPException(status_code=401, detail="Unauthorized")

        from superseded.server.worker import ReviewJob

        body = await request.json()
        try:
            owner = body["owner"]
            repo = body["repo"]
            pr_number = int(body["pr_number"])
            installation_id = int(body["installation_id"])
        except (KeyError, ValueError) as err:
            raise HTTPException(status_code=422, detail=f"Missing or invalid field: {err}") from err

        await store.init()
        installation = await store.get_installation(installation_id)
        if installation is None:
            raise HTTPException(status_code=404, detail="Installation not found")

        try:
            pr_info = await github.fetch_pr_info(
                token=await github.get_installation_token(installation_id),
                owner=owner,
                repo=repo,
                pr_number=pr_number,
            )
        except Exception as err:
            raise HTTPException(status_code=502, detail=f"Failed to fetch PR info: {err}") from err

        job = ReviewJob(
            installation_id=installation_id,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            head_sha=pr_info["head_sha"],
            base_sha=pr_info["base_sha"],
        )
        await worker.enqueue(job)
        logger.info(
            "manual_review_enqueued",
            extra={
                "repo": f"{owner}/{repo}",
                "pr": pr_number,
                "job_id": job.job_id,
            },
        )
        return {"status": "enqueued", "job_id": job.job_id}

    @app.post("/review/pr")
    async def review_pr(request: Request) -> Response:
        # Action-driven entry point. Authorization is the bearer api_key PLUS
        # resolution of an App installation for the repo (409 if absent) AND a
        # per-installation ``authorized_repos`` membership check — the same gate
        # the webhook path applies — so a single leaked api_key cannot be used to
        # drive reviews (and post PR comments / burn AI-CLI credits) against a
        # repo belonging to a different tenant of the same App installation.
        if not config.api_key:
            return Response(status_code=501, content="API key not configured on this server.")
        auth = request.headers.get("Authorization", "")
        expected = f"Bearer {config.api_key}"
        if not hmac.compare_digest(auth, expected):
            raise HTTPException(status_code=401, detail="Unauthorized")

        from superseded.server.worker import ReviewJob

        body = await request.json()
        try:
            owner = body["owner"]
            repo = body["repo"]
            pr_number = int(body["pr_number"])
        except (KeyError, ValueError) as err:
            raise HTTPException(status_code=422, detail=f"Missing or invalid field: {err}") from err

        passes_raw = body.get("passes")
        passes_list: list[str] | None = None
        if isinstance(passes_raw, str) and passes_raw.strip():
            passes_list = [p.strip() for p in passes_raw.split(",") if p.strip()]

        installation_id = await github.resolve_installation(owner, repo)
        if installation_id is None:
            raise HTTPException(
                status_code=409, detail="GitHub App is not installed on this repository."
            )

        await store.init()
        if not _is_repo_authorized(await store.get_installation(installation_id), owner, repo):
            raise HTTPException(
                status_code=403,
                detail="Repository is not authorized for this installation.",
            )

        try:
            token = await github.get_installation_token(installation_id)
            pr_info = await github.fetch_pr_info(
                token=token, owner=owner, repo=repo, pr_number=pr_number
            )
        except Exception as err:
            raise HTTPException(status_code=502, detail=f"Failed to fetch PR info: {err}") from err

        job = ReviewJob(
            installation_id=installation_id,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            head_sha=pr_info["head_sha"],
            base_sha=pr_info["base_sha"],
            passes=passes_list,
        )
        await worker.enqueue(job)
        logger.info(
            "review_pr_enqueued",
            extra={"repo": f"{owner}/{repo}", "pr": pr_number, "job_id": job.job_id},
        )
        return {"status": "enqueued", "job_id": job.job_id}

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

    @app.post("/webhook")
    async def webhook(request: Request, background_tasks: BackgroundTasks) -> Response:
        client_ip = request.client.host if request.client else "unknown"
        if not rate_limiter.allow(client_ip):
            return Response(status_code=429, content="Rate limit exceeded")

        payload = await request.body()
        signature = request.headers.get("X-Hub-Signature-256", "")

        if not github.verify_webhook(payload, signature):
            return Response(status_code=401, content="Invalid signature")

        if replay_limiter.is_replay(payload, signature):
            return Response(status_code=409, content="Duplicate webhook")

        event = request.headers.get("X-GitHub-Event", "")
        data = await request.json()

        if event == "pull_request":
            background_tasks.add_task(_handle_pr_event, data, github, worker, store)
        elif event == "installation":
            background_tasks.add_task(_handle_installation_event, data, store)
        elif event == "push":
            logger.info("webhook_push_received", extra={"ref": data.get("ref", "")})

        return Response(status_code=200, content="ok")

    return app


async def _handle_pr_event(
    data: dict,
    github: GitHubApp,
    worker: ReviewWorker,
    store: Store,
) -> None:
    action = data.get("action", "")
    if action not in ("opened", "synchronize", "reopened"):
        return

    try:
        pr = data["pull_request"]
        repo = data["repository"]
        owner = repo["owner"]["login"]
        repo_name = repo["name"]
        installation_id = data["installation"]["id"]
    except (KeyError, TypeError) as err:
        logger.warning("webhook_pr_event_missing_field", extra={"error": str(err)})
        return

    await store.init()
    installation = await store.get_installation(installation_id)
    if installation is None:
        logger.warning(
            "webhook_pr_unauthorized",
            extra={
                "installation_id": installation_id,
                "repo": f"{owner}/{repo_name}",
            },
        )
        return

    if not _is_repo_authorized(installation, owner, repo_name):
        logger.warning(
            "webhook_pr_repo_not_authorized",
            extra={
                "installation_id": installation_id,
                "repo": f"{owner}/{repo_name}",
                "authorized": json.loads(installation.get("repos", "[]")),
            },
        )
        return

    from superseded.server.worker import ReviewJob

    job = ReviewJob(
        installation_id=installation_id,
        owner=owner,
        repo=repo_name,
        pr_number=pr["number"],
        head_sha=pr["head"]["sha"],
        base_sha=pr["base"]["sha"],
    )
    await worker.enqueue(job)
    logger.info(
        "webhook_pr_enqueued",
        extra={"repo": f"{owner}/{repo_name}", "pr": pr["number"], "action": action},
    )


async def _handle_installation_event(
    data: dict,
    store: Store,
) -> None:
    action = data.get("action", "")
    installation = data["installation"]

    await store.init()

    if action == "created":
        repos = [r["name"] for r in data.get("repositories", [])]
        await store.record_installation(
            installation_id=installation["id"],
            owner=installation["account"]["login"],
            repos=repos,
        )
        logger.info(
            "installation_created",
            extra={"installation_id": installation["id"], "repos": repos},
        )
    elif action == "deleted":
        await store.remove_installation(installation["id"])
        logger.info(
            "installation_deleted",
            extra={"installation_id": installation["id"]},
        )
