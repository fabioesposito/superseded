from __future__ import annotations

from unittest.mock import AsyncMock, patch

from superseded.notifications import NotificationService


async def test_notify_sends_to_ntfy():
    service = NotificationService(topic="superseded-test", enabled=True)
    mock_response = AsyncMock()
    mock_response.status_code = 200

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        await service.notify(
            title="SUP-001: BUILD completed",
            message="Build took 2m 34s",
            priority="default",
            tags=["white_check_mark"],
        )
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "superseded-test" in call_args.args[0]
        assert call_args.kwargs["headers"]["Title"] == "SUP-001: BUILD completed"


async def test_notify_disabled_does_not_send():
    service = NotificationService(topic="superseded-test", enabled=False)
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        await service.notify(title="test", message="test", priority="default", tags=[])
        mock_post.assert_not_called()


async def test_notify_without_topic_does_not_send():
    service = NotificationService(topic="", enabled=True)
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        await service.notify(title="test", message="test", priority="default", tags=[])
        mock_post.assert_not_called()


async def test_notify_includes_click_url():
    service = NotificationService(topic="superseded-test", enabled=True)
    mock_response = AsyncMock()
    mock_response.status_code = 200

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        await service.notify(
            title="test",
            message="msg",
            priority="high",
            tags=["x"],
            click_url="http://localhost:8000/issues/SUP-001",
        )
        call_args = mock_post.call_args
        assert call_args.kwargs["headers"]["Click"] == "http://localhost:8000/issues/SUP-001"
        assert call_args.kwargs["headers"]["Priority"] == "high"
        assert call_args.kwargs["headers"]["Tags"] == "x"
