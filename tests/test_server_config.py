from __future__ import annotations

from pathlib import Path

import pytest

from superseded.server.config import ServerConfig


def test_server_config_is_configured():
    config = ServerConfig()
    assert not config.is_configured
    config = ServerConfig(app_id=123, webhook_secret="s", private_key_path=Path("/dev/null"))
    assert config.is_configured


def test_server_config_require_configured_rejects_default():
    config = ServerConfig()
    with pytest.raises(ValueError, match="not configured"):
        config.require_configured()


def test_server_config_require_configured_accepts_valid(tmp_path):
    key_file = tmp_path / "key.pem"
    key_file.write_text("k")
    config = ServerConfig(
        app_id=123,
        webhook_secret="s",
        private_key_path=key_file,
    )
    config.require_configured()


def test_server_config_defaults():
    config = ServerConfig()
    assert config.port == 8000
    assert config.host == "127.0.0.1"
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
    key_file = tmp_path / "key.pem"
    key_file.write_text("fake-private-key")
    config_file = tmp_path / "server.yaml"
    config_file.write_text(
        "app_id: 99999\n"
        "webhook_secret: whsec_yaml\n"
        f"private_key_path: {key_file}\n"
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


def test_server_config_tls_from_env(monkeypatch, tmp_path):
    key_file = tmp_path / "key.pem"
    key_file.write_text("k")
    cert_file = tmp_path / "cert.pem"
    cert_file.write_text("c")
    tls_key = tmp_path / "tls.key"
    tls_key.write_text("tk")
    tls_cert = tmp_path / "tls.crt"
    tls_cert.write_text("tc")

    monkeypatch.setenv("SUPERSEDED_APP_ID", "12345")
    monkeypatch.setenv("SUPERSEDED_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("SUPERSEDED_PRIVATE_KEY_PATH", str(key_file))
    monkeypatch.setenv("SUPERSEDED_TLS_CERT", str(tls_cert))
    monkeypatch.setenv("SUPERSEDED_TLS_KEY", str(tls_key))

    config = ServerConfig.from_env()
    assert config.tls_cert_path == tls_cert
    assert config.tls_key_path == tls_key


def test_server_config_tls_defaults_to_none():
    config = ServerConfig()
    assert config.tls_cert_path is None
    assert config.tls_key_path is None
