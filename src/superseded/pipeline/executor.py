from __future__ import annotations

import logging

from superseded.config import SupersededConfig
from superseded.db import Database
from superseded.harness import Harness
from superseded.models import Issue, Stage, StageResult
from superseded.notifications import NotificationService
from superseded.pipeline.harness import HarnessRunner
from superseded.pipeline.worktree import WorktreeManager

logger = logging.getLogger(__name__)


class StageExecutor:
    """Backward-compatible wrapper around Harness."""

    def __init__(
        self,
        runner: HarnessRunner,
        db: Database,
        worktree_manager: WorktreeManager,
        notification_service: NotificationService | None = None,
    ) -> None:
        self._harness = Harness(
            repo_path=runner.repo_path,
            agent_factory=runner.agent_factory,
            stage_configs=runner.stage_configs,
            event_manager=runner.event_manager,
            db=db,
            notification_service=notification_service,
        )
        self.runner = runner
        self.db = db
        self.worktree_manager = worktree_manager
        self.notification_service = notification_service

    async def run_stage(self, issue: Issue, stage: Stage, config: SupersededConfig) -> StageResult:
        return await self._harness.run_stage(issue, stage, config)
