from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(
        self,
        topic: str,
        enabled: bool = False,
        slack_webhook_url: str = "",
        webhook_url: str = "",
        webhook_headers: dict[str, str] | None = None,
    ) -> None:
        self.topic = topic
        self.enabled = enabled
        self._base_url = "https://ntfy.sh"
        self._slack_webhook_url = slack_webhook_url
        self._webhook_url = webhook_url
        self._webhook_headers = webhook_headers or {}

    async def notify(
        self,
        title: str,
        message: str = "",
        priority: str = "default",
        tags: list[str] | None = None,
        click_url: str | None = None,
    ) -> None:
        if not self.enabled:
            return

        if self.topic:
            await self._send_ntfy(title, message, priority, tags, click_url)
        if self._slack_webhook_url:
            await self._send_slack(title, message)
        if self._webhook_url:
            await self._send_webhook(title, message)

    async def _send_ntfy(self, title, message, priority, tags, click_url):
        headers: dict[str, str] = {"Title": title, "Priority": priority}
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

    async def _send_slack(self, title, message):
        payload = {"text": f"*{title}*\n{message}"}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(self._slack_webhook_url, json=payload)
        except httpx.HTTPError:
            logger.warning("Failed to send Slack notification")

    async def _send_webhook(self, title, message):
        payload = {"title": title, "message": message}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(self._webhook_url, json=payload, headers=self._webhook_headers)
        except httpx.HTTPError:
            logger.warning("Failed to send webhook notification")
