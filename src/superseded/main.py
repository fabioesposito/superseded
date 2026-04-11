from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from pathlib import Path

import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from superseded.config import SupersededConfig, load_config
from superseded.db import Database
from superseded.routes.dashboard import router as dashboard_router
from superseded.routes.issues import router as issues_router
from superseded.routes.pipeline import router as pipeline_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db: Database = app.state.db
    await db.initialize()
    yield
    await db.close()


def create_app(
    repo_path: str | None = None, config: SupersededConfig | None = None
) -> FastAPI:
    if config is None:
        if repo_path is None:
            repo_path = str(Path.cwd())
        config = load_config(Path(repo_path))

    app = FastAPI(title="Superseded", version="0.1.0", lifespan=lifespan)

    app.state.config = config
    app.state.db = Database(str(Path(config.repo_path) / config.db_path))

    static_dir = Path(__file__).parent.parent.parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    app.include_router(dashboard_router)
    app.include_router(issues_router)
    app.include_router(pipeline_router)

    return app


def cli() -> None:
    parser = argparse.ArgumentParser(
        description="Superseded - local-first agentic pipeline tool"
    )
    parser.add_argument(
        "repo_path", nargs="?", default=".", help="Path to the git repository"
    )
    parser.add_argument(
        "--port", type=int, default=None, help="Port to run the server on"
    )
    parser.add_argument("--host", type=str, default=None, help="Host to bind to")
    args = parser.parse_args()

    config = load_config(Path(args.repo_path).resolve())
    port = args.port or config.port
    host = args.host or config.host

    import uvicorn

    uvicorn.run(
        f"superseded.main:create_app", host=host, port=port, factory=True, reload=False
    )
