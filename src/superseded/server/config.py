from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator


class ServerConfig(BaseModel):
    app_id: int = 0
    webhook_secret: str = ""
    private_key_path: Path = Path("/dev/null")
    port: int = 8000
    host: str = "127.0.0.1"
    max_concurrent_reviews: int = 3
    temp_dir: Path = Path("/tmp/superseded")
    log_level: str = "info"
    health_token: str = ""
    database_url: str | None = None
    tls_cert_path: Path | None = None
    tls_key_path: Path | None = None
    agent: str | None = None
    model: str | None = None

    @property
    def is_configured(self) -> bool:
        """Return True if this config has a real app_id (not default 0)."""
        return self.app_id != 0

    def require_configured(self) -> None:
        """Raise ValueError if the server is not fully configured for production.

        Guards against booting a webhook receiver with a forgeable empty
        webhook secret / missing app credentials.
        """
        if not self.is_configured:
            raise ValueError(
                "Server is not configured: set SUPERSEDED_APP_ID, "
                "SUPERSEDED_WEBHOOK_SECRET, and SUPERSEDED_PRIVATE_KEY_PATH "
                "(or provide them in the YAML config)."
            )
        if self.host not in ("127.0.0.1", "localhost") and not (
            self.tls_cert_path and self.tls_key_path
        ):
            raise ValueError(
                f"Binding to {self.host} requires TLS. Set SUPERSEDED_TLS_CERT "
                "and SUPERSEDED_TLS_KEY, or use --host 127.0.0.1."
            )

    @model_validator(mode="after")
    def _validate_required_fields(self) -> ServerConfig:
        if self.app_id == 0:
            return self
        if self.app_id < 0:
            raise ValueError("app_id must be a positive integer")
        if not self.webhook_secret:
            raise ValueError("webhook_secret must not be empty")
        if not self.private_key_path.exists():
            raise ValueError(f"private_key_path does not exist: {self.private_key_path}")
        return self

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

        pkey = Path(private_key_path)
        if not pkey.exists():
            raise ValueError(f"private_key_path does not exist: {pkey}")

        kwargs: dict = {
            "app_id": int(app_id),
            "webhook_secret": webhook_secret,
            "private_key_path": pkey,
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

        tls_cert = os.environ.get("SUPERSEDED_TLS_CERT")
        tls_key = os.environ.get("SUPERSEDED_TLS_KEY")
        if tls_cert:
            kwargs["tls_cert_path"] = Path(tls_cert)
        if tls_key:
            kwargs["tls_key_path"] = Path(tls_key)

        health_token = os.environ.get("SUPERSEDED_HEALTH_TOKEN")
        if health_token:
            kwargs["health_token"] = health_token

        database_url = os.environ.get("SUPERSEDED_DATABASE_URL")
        if database_url:
            kwargs["database_url"] = database_url

        agent = os.environ.get("SUPERSEDED_SERVER_AGENT")
        if agent:
            kwargs["agent"] = agent

        model = os.environ.get("SUPERSEDED_SERVER_MODEL")
        if model:
            kwargs["model"] = model

        return cls(**kwargs)

    @classmethod
    def from_yaml(cls, path: Path) -> ServerConfig:
        data = yaml.safe_load(path.read_text()) or {}
        if "private_key_path" in data:
            pkey = Path(data["private_key_path"])
            if not pkey.exists():
                raise ValueError(f"private_key_path does not exist: {pkey}")
            data["private_key_path"] = pkey
        if "temp_dir" in data:
            data["temp_dir"] = Path(data["temp_dir"])
        return cls(**data)
