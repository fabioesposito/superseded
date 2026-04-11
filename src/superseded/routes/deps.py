from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from superseded.config import SupersededConfig
from superseded.db import Database


@dataclass
class Deps:
    config: SupersededConfig
    db: Database


async def get_deps(request: Request) -> Deps:
    return Deps(
        config=request.app.state.config,
        db=request.app.state.db,
    )
