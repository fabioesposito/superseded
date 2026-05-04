from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from superseded.agents.factory import AgentFactory
from superseded.config import SupersededConfig, load_config
from superseded.db import Database
from superseded.models import IssueStatus, Stage
from superseded.notifications import NotificationService
from superseded.pipeline.events import PipelineEventManager
from superseded.pipeline.executor import StageExecutor
from superseded.pipeline.harness import HarnessRunner
from superseded.pipeline.worktree import WorktreeManager
from superseded.routes.api.pipeline import api_router as pipeline_api_router
from superseded.routes.auth import AuthMiddleware
from superseded.routes.csrf import CsrfMiddleware
from superseded.routes.service import PipelineState
from superseded.routes.web.dashboard import router as dashboard_router
from superseded.routes.web.issues import router as issues_router
from superseded.routes.web.pipeline import router as pipeline_router
from superseded.routes.web.settings import router as settings_router
from superseded.state_writer import IssueStateWriter

logger = logging.getLogger(__name__)


async def _recover_in_progress(app: FastAPI) -> None:
    """Check for in-progress issues on startup and mark them for retry."""
    db: Database = app.state.db
    issues = await db.list_issues(offset=0, limit=1000)
    recovered = 0
    for issue_data in issues:
        if issue_data["status"] == "in-progress":
            issue_id = issue_data["id"]
            pipeline = app.state.pipeline
            harness = pipeline.executor._harness
            stage = issue_data.get("stage", "spec")
            checkpoint = harness.checkpoint_manager.load(issue_id, stage)
            if checkpoint:
                logger.info("Recovering %s from checkpoint at stage %s", issue_id, stage)
                await db.update_pause_reason(issue_id, "recovered-from-crash")
            else:
                logger.info("Marking %s as paused (no checkpoint found)", issue_id)
                await db.update_pause_reason(issue_id, "server-restarted")
                writer = IssueStateWriter(db)
                filepath = issue_data.get("filepath", "")
                await writer.write_status(
                    issue_id, filepath, IssueStatus.PAUSED, Stage.by_value(stage)
                )
            recovered += 1
    if recovered:
        logger.info("Recovered %d in-progress issues on startup", recovered)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db: Database = app.state.db
    await db.initialize()

    pipeline = getattr(app.state, "pipeline", None)
    if pipeline:
        harness = pipeline.executor._harness
        loop = asyncio.get_event_loop()
        harness.lifecycle_manager.install_signal_handlers(loop)

    await _recover_in_progress(app)

    yield

    if pipeline:
        harness = pipeline.executor._harness
        await harness.lifecycle_manager.graceful_shutdown(timeout=10.0)
        harness.lifecycle_manager.restore_signal_handlers()

    await db.close()


def _build_pipeline_state(config: SupersededConfig, db: Database) -> PipelineState:
    import asyncio

    event_manager = PipelineEventManager()
    factory = AgentFactory(
        default_agent=config.default_agent,
        default_model=config.default_model,
        timeout=config.stage_timeout_seconds,
        github_token=config.github_token,
        openai_api_key=config.openai_api_key,
        anthropic_api_key=config.anthropic_api_key,
        opencode_api_key=config.opencode_api_key,
        rtk=config.rtk,
    )
    notification_service = NotificationService(
        topic=config.notifications.ntfy_topic,
        enabled=config.notifications.enabled,
    )
    runner = HarnessRunner(
        agent_factory=factory,
        repo_path=config.repo_path,
        event_manager=event_manager,
        stage_configs=config.stages,
    )
    runner._harness.db = db
    runner._harness.notification_service = notification_service
    worktree_manager = WorktreeManager(config.repo_path)
    if config.repos:
        runner.configure_repos(config.repos)
        for name, entry in config.repos.items():
            worktree_manager.register_repo(name, entry.path, entry.git_url)
    executor = StageExecutor(
        runner=runner,
        db=db,
        worktree_manager=worktree_manager,
        notification_service=notification_service,
    )
    return PipelineState(
        executor=executor,
        event_manager=event_manager,
        running_issues=set(),
        running_lock=asyncio.Lock(),
    )


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

    app.state.pipeline = _build_pipeline_state(config, app.state.db)

    static_dir = Path(__file__).parent.parent.parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/health")
    async def health():
        pipeline = getattr(app.state, "pipeline", None)
        result: dict = {"status": "ok"}
        if pipeline:
            running = list(pipeline.running_issues)
            result["running_issues"] = running
            result["active_stages"] = len(running)
            if pipeline.executor._harness.lifecycle_manager.is_shutting_down():
                result["status"] = "shutting-down"
        return result

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
    parser.add_argument(
        "repo_path", nargs="?", default=".", help="Path to the git repository (or 'init')"
    )
    parser.add_argument("--port", type=int, default=None, help="Port to run the server on")
    parser.add_argument("--host", type=str, default=None, help="Host to bind to")
    args = parser.parse_args()

    if args.repo_path == "init" and not Path("init").is_dir():
        from superseded.cli import init_command

        init_command(Path(".").resolve())
        print("Initialized .superseded/ in current directory")
        return

    config = load_config(Path(args.repo_path).resolve())

    from superseded.config import validate_config

    errors = validate_config(config)
    if errors:
        print("Configuration errors:")
        for err in errors:
            print(f"  - {err}")
        print("\nRun 'superseded init' or edit .superseded/config.yaml to fix.")
        sys.exit(1)

    port = args.port or config.port
    host = args.host or config.host

    import uvicorn

    uvicorn.run("superseded.main:create_app", host=host, port=port, factory=True, reload=False)
