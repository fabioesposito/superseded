from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel


class ServerConfig(BaseModel):
    app_id: int = 0
    webhook_secret: str = ""
    private_key_path: Path = Path("/dev/null")
    port: int = 8000
    host: str = "0.0.0.0"
    max_concurrent_reviews: int = 3
    temp_dir: Path = Path("/tmp/superseded")
    log_level: str = "info"

    @classmethod
    def from_env(cls) -> ServerConfig:
        app_id = os.environ.get("SUPERSEDED_APP_ID")
        webhook_secret = os.environ.get("SUPERSEDED_WEBHOOK_SECRET")
        private_key_path = os.environ.get("SUPERSEDED_PRIVATE_KEY_PATH")

        if not app_id or not webhook_secret or not private_key_path:
            raise ValueError(
                "SUPERSEDED_APP_ID, SUPERSEDED_WEBHOOK_SECRET, and "
                "SUPERSEDED_PRIVATE_KEY_PATH are required"
            )

        kwargs: dict = {
            "app_id": int(app_id),
            "webhook_secret": webhook_secret,
            "private_key_path": Path(private_key_path),
        }

        max_concurrent = os.environ.get("SUPERSEDED_MAX_CONCURRENT")
        if max_concurrent:
            kwargs["max_concurrent_reviews"] = int(max_concurrent)

        port = os.environ.get("SUPERSEDED_PORT")
        if port:
            kwargs["port"] = int(port)

        host = os.environ.get("SUPERSEDED_HOST")
        if host:
            kwargs["host"] = host

        log_level = os.environ.get("SUPERSEDED_LOG_LEVEL")
        if log_level:
            kwargs["log_level"] = log_level

        return cls(**kwargs)

    @classmethod
    def from_yaml(cls, path: Path) -> ServerConfig:
        data = yaml.safe_load(path.read_text()) or {}
        if "private_key_path" in data:
            data["private_key_path"] = Path(data["private_key_path"])
        if "temp_dir" in data:
            data["temp_dir"] = Path(data["temp_dir"])
        return cls(**data)
