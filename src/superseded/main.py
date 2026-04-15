from __future__ import annotations

import argparse
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from superseded.agents.factory import AgentFactory
from superseded.config import SupersededConfig, load_config
from superseded.db import Database
from superseded.notifications import NotificationService
from superseded.pipeline.events import PipelineEventManager
from superseded.pipeline.executor import StageExecutor
from superseded.pipeline.harness import HarnessRunner
from superseded.pipeline.worktree import WorktreeManager
from superseded.routes.api.pipeline import api_router as pipeline_api_router
from superseded.routes.auth import AuthMiddleware
from superseded.routes.csrf import CsrfMiddleware
from superseded.routes.deps import PipelineState
from superseded.routes.web.dashboard import router as dashboard_router
from superseded.routes.web.issues import router as issues_router
from superseded.routes.web.pipeline import router as pipeline_router
from superseded.routes.web.settings import router as settings_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db: Database = app.state.db
    await db.initialize()
    yield
    await db.close()


def _build_pipeline_state(config: SupersededConfig) -> PipelineState:
    event_manager = PipelineEventManager()
    factory = AgentFactory(
        default_agent=config.default_agent,
        default_model=config.default_model,
        timeout=config.stage_timeout_seconds,
        github_token=config.github_token,
        openai_api_key=config.openai_api_key,
        anthropic_api_key=config.anthropic_api_key,
        opencode_api_key=config.opencode_api_key,
    )
    runner = HarnessRunner(
        agent_factory=factory,
        repo_path=config.repo_path,
        max_retries=config.max_retries,
        retryable_stages=config.retryable_stages,
        event_manager=event_manager,
        stage_configs=config.stages,
    )
    worktree_manager = WorktreeManager(config.repo_path, source_code_root=config.source_code_root)
    if config.repos:
        runner.configure_repos(config.repos)
        for name, entry in config.repos.items():
            worktree_manager.register_repo(name, entry.path, entry.git_url)
    notification_service = NotificationService(
        topic=config.notifications.ntfy_topic,
        enabled=config.notifications.enabled,
    )
    executor = StageExecutor(
        runner=runner,
        db=None,
        worktree_manager=worktree_manager,
        notification_service=notification_service,
    )
    return PipelineState(executor=executor, event_manager=event_manager)


def create_app(
    repo_path: str | None = None,
    config: SupersededConfig | None = None,
    db: Database | None = None,
) -> FastAPI:
    if config is None:
        if repo_path is None:
            repo_path = str(Path.cwd())
        config = load_config(Path(repo_path))

    app = FastAPI(title="Superseded", version="0.1.0", lifespan=lifespan)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(CsrfMiddleware)

    app.state.config = config
    if db is not None:
        app.state.db = db
    else:
        app.state.db = Database(str(Path(config.repo_path) / config.db_path))

    app.state.pipeline = _build_pipeline_state(config)
    app.state.pipeline.executor.db = app.state.db

    static_dir = Path(__file__).parent.parent.parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics_redirect():
        return RedirectResponse(url="/pipeline/metrics")

    app.include_router(dashboard_router)
    app.include_router(issues_router)
    app.include_router(pipeline_api_router)
    app.include_router(pipeline_router)
    app.include_router(settings_router)

    return app


def cli() -> None:
    parser = argparse.ArgumentParser(description="Superseded - local-first agentic pipeline tool")
    parser.add_argument("repo_path", nargs="?", default=".", help="Path to the git repository")
    parser.add_argument("--port", type=int, default=None, help="Port to run the server on")
    parser.add_argument("--host", type=str, default=None, help="Host to bind to")
    args = parser.parse_args()

    config = load_config(Path(args.repo_path).resolve())
    port = args.port or config.port
    host = args.host or config.host

    import uvicorn

    uvicorn.run("superseded.main:create_app", host=host, port=port, factory=True, reload=False)
