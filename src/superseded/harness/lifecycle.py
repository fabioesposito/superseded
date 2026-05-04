from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
import signal
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class HealthStatus:
    issue_id: str
    alive: bool
    last_output_time: str | None
    silence_duration_seconds: float
    status: str  # "healthy", "silent", "dead"


@dataclass
class ResourceLimits:
    max_tokens: int = 0
    max_wall_time_seconds: int = 0
    max_cost_usd: float = 0.0


class LifecycleManager:
    def __init__(self) -> None:
        self._running_processes: dict[str, asyncio.subprocess.Process] = {}
        self._last_output: dict[str, datetime.datetime] = {}
        self._shutdown_event = asyncio.Event()
        self._original_handlers: dict = {}

    def record_output(self, issue_id: str) -> None:
        """Record that an agent produced output at this time."""
        self._last_output[issue_id] = datetime.datetime.now(datetime.UTC)

    def check_health(self, issue_id: str, silence_threshold: float = 300.0) -> HealthStatus:
        """Check health of a running agent."""
        proc = self._running_processes.get(issue_id)
        alive = proc is not None and proc.returncode is None

        last_output = self._last_output.get(issue_id)
        silence_duration = 0.0
        if last_output:
            silence_duration = (datetime.datetime.now(datetime.UTC) - last_output).total_seconds()

        if not alive:
            status = "dead"
        elif silence_duration > silence_threshold:
            status = "silent"
        else:
            status = "healthy"

        return HealthStatus(
            issue_id=issue_id,
            alive=alive,
            last_output_time=last_output.isoformat() if last_output else None,
            silence_duration_seconds=silence_duration,
            status=status,
        )

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
