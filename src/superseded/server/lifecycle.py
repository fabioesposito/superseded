from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from superseded.server.app import FastAPI
    from superseded.server.worker import ReviewWorker

logger = logging.getLogger(__name__)


class ServerLifecycle:
    def __init__(self, app: FastAPI, worker: ReviewWorker) -> None:
        self.app = app
        self.worker = worker
        self._shutdown_event = asyncio.Event()
        self._worker_task: asyncio.Task | None = None
        self._shutdown_task: asyncio.Task | None = None

    async def startup(self) -> None:
        logger.info("Starting Superseded server...")
        self._worker_task = asyncio.create_task(self.worker.run())

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._handle_signal)

        logger.info("Server started")

    async def shutdown(self) -> None:
        logger.info("Shutting down...")
        self._shutdown_event.set()

        if self._worker_task is not None:
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task

        logger.info("Server stopped")

    def _handle_signal(self) -> None:
        logger.info("Received shutdown signal")
        self._shutdown_task = asyncio.create_task(self.shutdown())
