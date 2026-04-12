from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from superseded.models import AgentEvent


class PipelineEventManager:
    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[AgentEvent | None]] = {}

    def start(self, issue_id: str) -> None:
        self._queues[issue_id] = asyncio.Queue()

    def stop(self, issue_id: str) -> None:
        queue = self._queues.pop(issue_id, None)
        if queue:
            _task = asyncio.create_task(queue.put(None))  # noqa: RUF006

    async def publish(self, issue_id: str, event: AgentEvent) -> None:
        queue = self._queues.get(issue_id)
        if queue is None:
            raise KeyError(f"No active session for issue {issue_id}")
        await queue.put(event)

    async def subscribe(self, issue_id: str) -> AsyncIterator[AgentEvent]:
        queue = self._queues.get(issue_id)
        if queue is None:
            return
        while True:
            event = await queue.get()
            if event is None:
                return
            yield event
