from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, topic: str, enabled: bool = False) -> None:
        self.topic = topic
        self.enabled = enabled
        self._base_url = "https://ntfy.sh"

    async def notify(
        self,
        title: str,
        message: str = "",
        priority: str = "default",
        tags: list[str] | None = None,
        click_url: str | None = None,
    ) -> None:
        if not self.enabled or not self.topic:
            return
        headers: dict[str, str] = {
            "Title": title,
            "Priority": priority,
        }
        if tags:
            headers["Tags"] = ",".join(tags)
        if click_url:
            headers["Click"] = click_url
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"{self._base_url}/{self.topic}",
                    content=message,
                    headers=headers,
                )
        except httpx.HTTPError:
            logger.warning("Failed to send notification to ntfy.sh")
