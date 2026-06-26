from __future__ import annotations

import hashlib
import hmac
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response

if TYPE_CHECKING:
    from superseded.memory.store import MemoryStore
    from superseded.server.config import ServerConfig
    from superseded.server.github import GitHubApp
    from superseded.server.repo_manager import RepoManager
    from superseded.server.worker import ReviewWorker

logger = logging.getLogger(__name__)

WEBHOOK_RATE_LIMIT = 60
WEBHOOK_RATE_WINDOW = 60.0
REPLAY_WINDOW = 300.0


class ReplayProtector:
    """Reject duplicate webhook payloads within a sliding time window."""

    def __init__(self, window: float = REPLAY_WINDOW) -> None:
        self._window = window
        self._recent: dict[str, float] = {}

    def is_replay(self, payload: bytes, signature: str) -> bool:
        now = time.time()
        cutoff = now - self._window
        self._recent = {k: v for k, v in self._recent.items() if v > cutoff}
        sig_hash = hashlib.sha256(
            (signature + payload.decode(errors="replace")).encode()
        ).hexdigest()
        if sig_hash in self._recent:
            return True
        self._recent[sig_hash] = now
        return False


class RateLimiter:
    """Simple in-memory sliding-window rate limiter per client IP."""

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
    store: MemoryStore,
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
    async def manual_review() -> Response:
        return Response(
            status_code=501,
            content="Manual review trigger is not yet implemented (API-key auth is future work).",
        )

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
    store: MemoryStore,
) -> None:
    action = data.get("action", "")
    if action not in ("opened", "synchronize", "reopened"):
        return

    pr = data["pull_request"]
    repo = data["repository"]
    owner = repo["owner"]["login"]
    repo_name = repo["name"]
    installation_id = data["installation"]["id"]

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

    import json

    authorized_repos = json.loads(installation.get("repos", "[]"))
    full_name = f"{owner}/{repo_name}"
    if authorized_repos and full_name not in authorized_repos and repo_name not in authorized_repos:
        logger.warning(
            "webhook_pr_repo_not_authorized",
            extra={
                "installation_id": installation_id,
                "repo": full_name,
                "authorized": authorized_repos,
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
    store: MemoryStore,
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
