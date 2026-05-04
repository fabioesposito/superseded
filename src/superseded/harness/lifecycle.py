from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ResourceLimits:
    max_tokens: int = 0
    max_wall_time_seconds: int = 0
    max_cost_usd: float = 0.0


class LifecycleManager:
    def __init__(self) -> None:
        self._running_processes: dict[str, asyncio.subprocess.Process] = {}
        self._shutdown_event = asyncio.Event()
        self._original_handlers: dict = {}

    def register_process(self, issue_id: str, process: asyncio.subprocess.Process) -> None:
        self._running_processes[issue_id] = process

    def unregister_process(self, issue_id: str) -> None:
        self._running_processes.pop(issue_id, None)

    async def graceful_shutdown(self, timeout: float = 30.0) -> None:
        """Signal all running processes to stop, wait, then force-kill."""
        logger.info("Initiating graceful shutdown with %ds timeout", int(timeout))
        self._shutdown_event.set()

        for issue_id, proc in self._running_processes.items():
            if proc.returncode is None:
                logger.info("Sending SIGTERM to process for %s", issue_id)
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()

        await asyncio.sleep(timeout)

        for issue_id, proc in list(self._running_processes.items()):
            if proc.returncode is None:
                logger.warning("Force-killing process for %s", issue_id)
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()

        self._running_processes.clear()

    def is_shutting_down(self) -> bool:
        return self._shutdown_event.is_set()

    def install_signal_handlers(self, loop: asyncio.AbstractEventLoop) -> None:
        """Install SIGTERM/SIGINT handlers for graceful shutdown."""
        for sig in (signal.SIGTERM, signal.SIGINT):
            self._original_handlers[sig] = signal.getsignal(sig)
            loop.add_signal_handler(sig, lambda: asyncio.ensure_future(self.graceful_shutdown()))

    def restore_signal_handlers(self) -> None:
        for sig, handler in self._original_handlers.items():
            signal.signal(sig, handler)
        self._running_processes.clear()

    def check_resource_limits(
        self,
        limits: ResourceLimits,
        tokens_used: int = 0,
        wall_time: float = 0.0,
        cost: float = 0.0,
    ) -> str | None:
        """Check if resource limits are exceeded. Returns error message or None."""
        if limits.max_tokens > 0 and tokens_used > limits.max_tokens:
            return f"Token limit exceeded: {tokens_used} > {limits.max_tokens}"
        if limits.max_wall_time_seconds > 0 and wall_time > limits.max_wall_time_seconds:
            return f"Wall time limit exceeded: {int(wall_time)}s > {limits.max_wall_time_seconds}s"
        if limits.max_cost_usd > 0 and cost > limits.max_cost_usd:
            return f"Cost limit exceeded: ${cost:.2f} > ${limits.max_cost_usd:.2f}"
        return None
