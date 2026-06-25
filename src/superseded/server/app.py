from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING

from fastapi import BackgroundTasks, FastAPI, Request, Response

if TYPE_CHECKING:
    from superseded.memory.store import MemoryStore
    from superseded.server.config import ServerConfig
    from superseded.server.github import GitHubApp
    from superseded.server.repo_manager import RepoManager
    from superseded.server.worker import ReviewWorker

logger = logging.getLogger(__name__)


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

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "queue_depth": worker.queue.qsize(),
            "active_reviews": worker.active_count,
            "disk_usage": repo_manager.disk_usage(),
            "uptime_seconds": time.time() - start_time,
        }

    @app.post("/review")
    async def manual_review() -> Response:
        return Response(
            status_code=501,
            content="Manual review trigger is not yet implemented (API-key auth is future work).",
        )

    @app.post("/webhook")
    async def webhook(request: Request, background_tasks: BackgroundTasks) -> Response:
        payload = await request.body()
        signature = request.headers.get("X-Hub-Signature-256", "")

        if not github.verify_webhook(payload, signature):
            return Response(status_code=401, content="Invalid signature")

        event = request.headers.get("X-GitHub-Event", "")
        data = await request.json()

        if event == "pull_request":
            background_tasks.add_task(_handle_pr_event, data, github, worker)
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
) -> None:
    action = data.get("action", "")
    if action not in ("opened", "synchronize", "reopened"):
        return

    pr = data["pull_request"]
    repo = data["repository"]
    owner = repo["owner"]["login"]
    repo_name = repo["name"]

    from superseded.server.worker import ReviewJob

    job = ReviewJob(
        installation_id=data["installation"]["id"],
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
