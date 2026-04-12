from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from superseded.config import SupersededConfig
from superseded.db import Database
from superseded.pipeline.events import PipelineEventManager
from superseded.pipeline.executor import StageExecutor


@dataclass
class PipelineState:
    executor: StageExecutor
    event_manager: PipelineEventManager


@dataclass
class Deps:
    config: SupersededConfig
    db: Database
    pipeline: PipelineState | None = None


async def get_deps(request: Request) -> Deps:
    return Deps(
        config=request.app.state.config,
        db=request.app.state.db,
        pipeline=getattr(request.app.state, "pipeline", None),
    )
