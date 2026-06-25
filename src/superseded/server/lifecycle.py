from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from superseded.server.worker import ReviewWorker

logger = logging.getLogger(__name__)

_RESERVED_LOG_FIELDS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "getMessage",
    }
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "event": record.getMessage(),
            "level": record.levelname,
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_FIELDS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ServerLifecycle:
    def __init__(
        self,
        worker: ReviewWorker,
        shutdown_timeout: float = 10.0,
        app: object | None = None,
    ) -> None:
        # ``app`` is accepted for backward compatibility; the lifecycle no
        # longer needs an app reference (FastAPI drives startup/shutdown via
        # the lifespan context manager supplied by the CLI).
        self.app = app
        self.worker = worker
        self.shutdown_timeout = shutdown_timeout
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
            try:
                await asyncio.wait_for(self.worker.queue.join(), timeout=self.shutdown_timeout)
            except TimeoutError:
                logger.warning(
                    "Shutdown timeout reached with %d job(s) still in queue",
                    self.worker.queue.qsize(),
                )
            unprocessed = self.worker.queue.qsize()
            if unprocessed:
                logger.warning("%d unprocessed job(s) in queue at shutdown", unprocessed)

            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task

        logger.info("Server stopped")

    def _handle_signal(self) -> None:
        logger.info("Received shutdown signal")
        self._shutdown_task = asyncio.create_task(self.shutdown())
