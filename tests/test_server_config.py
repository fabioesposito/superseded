from __future__ import annotations

import pytest

from superseded.server.config import ServerConfig


def test_server_config_defaults():
    config = ServerConfig()
    assert config.port == 8000
    assert config.host == "0.0.0.0"
    assert config.max_concurrent_reviews == 3
    assert config.log_level == "info"


def test_server_config_from_env(monkeypatch, tmp_path):
    key_file = tmp_path / "key.pem"
    key_file.write_text("fake-private-key")
    monkeypatch.setenv("SUPERSEDED_APP_ID", "12345")
    monkeypatch.setenv("SUPERSEDED_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("SUPERSEDED_PRIVATE_KEY_PATH", str(key_file))
    monkeypatch.setenv("SUPERSEDED_MAX_CONCURRENT", "5")
    monkeypatch.setenv("SUPERSEDED_PORT", "9000")

    config = ServerConfig.from_env()
    assert config.app_id == 12345
    assert config.webhook_secret == "whsec_test"
    assert config.private_key_path == key_file
    assert config.max_concurrent_reviews == 5
    assert config.port == 9000


def test_server_config_from_env_missing(monkeypatch):
    monkeypatch.delenv("SUPERSEDED_APP_ID", raising=False)
    monkeypatch.delenv("SUPERSEDED_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("SUPERSEDED_PRIVATE_KEY_PATH", raising=False)

    with pytest.raises((ValueError, Exception)):
        ServerConfig.from_env()


def test_server_config_from_yaml(tmp_path):
    config_file = tmp_path / "server.yaml"
    config_file.write_text(
        "app_id: 99999\n"
        "webhook_secret: whsec_yaml\n"
        "private_key_path: /etc/key.pem\n"
        "max_concurrent_reviews: 10\n"
        "port: 3000\n"
        "host: 127.0.0.1\n"
        "log_level: debug\n"
    )
    config = ServerConfig.from_yaml(config_file)
    assert config.app_id == 99999
    assert config.webhook_secret == "whsec_yaml"
    assert config.max_concurrent_reviews == 10
    assert config.port == 3000
    assert config.host == "127.0.0.1"
    assert config.log_level == "debug"
