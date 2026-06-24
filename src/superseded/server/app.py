from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, Response

if TYPE_CHECKING:
    from superseded.server.config import ServerConfig
    from superseded.server.github import GitHubApp
    from superseded.server.worker import ReviewWorker

logger = logging.getLogger(__name__)


def create_app(
    config: ServerConfig,
    github: GitHubApp,
    worker: ReviewWorker,
) -> FastAPI:
    app = FastAPI(title="Superseded", version="0.1.0")

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "queue_depth": worker.queue.qsize(),
            "active_reviews": worker.active_count,
        }

    @app.post("/webhook")
    async def webhook(request: Request) -> Response:
        payload = await request.body()
        signature = request.headers.get("X-Hub-Signature-256", "")

        if not github.verify_webhook(payload, signature):
            return Response(status_code=401, content="Invalid signature")

        event = request.headers.get("X-GitHub-Event", "")
        data = await request.json()

        if event == "pull_request":
            await _handle_pr_event(data, github, worker)
        elif event == "installation":
            await _handle_installation_event(data, github)

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
    github: GitHubApp,
) -> None:
    action = data.get("action", "")
    installation = data["installation"]

    from superseded.memory.store import MemoryStore

    store = MemoryStore()
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
