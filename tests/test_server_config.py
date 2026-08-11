from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from superseded.server.config import ServerConfig


def _set_required_server_env(monkeypatch):
    monkeypatch.setenv("SUPERSEDED_APP_ID", "123456")
    monkeypatch.setenv("SUPERSEDED_WEBHOOK_SECRET", "whs")
    pk = Path(tempfile.mkstemp(suffix=".pem")[1])
    pk.write_text("dummy")
    monkeypatch.setenv("SUPERSEDED_PRIVATE_KEY_PATH", str(pk))


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


def test_server_config_database_url_defaults_to_none():
    from superseded.server.config import ServerConfig

    assert ServerConfig().database_url is None


def test_server_config_database_url_from_env(monkeypatch, tmp_path):
    from superseded.server.config import ServerConfig

    key = tmp_path / "key.pem"
    key.write_text("x")
    monkeypatch.setenv("SUPERSEDED_APP_ID", "111")
    monkeypatch.setenv("SUPERSEDED_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("SUPERSEDED_PRIVATE_KEY_PATH", str(key))
    monkeypatch.setenv("SUPERSEDED_DATABASE_URL", "postgres://u:p@h/db")
    cfg = ServerConfig.from_env()
    assert cfg.database_url == "postgres://u:p@h/db"


def test_server_config_database_url_absent_from_env(monkeypatch, tmp_path):
    from superseded.server.config import ServerConfig

    key = tmp_path / "key.pem"
    key.write_text("x")
    monkeypatch.setenv("SUPERSEDED_APP_ID", "111")
    monkeypatch.setenv("SUPERSEDED_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("SUPERSEDED_PRIVATE_KEY_PATH", str(key))
    monkeypatch.delenv("SUPERSEDED_DATABASE_URL", raising=False)
    cfg = ServerConfig.from_env()
    assert cfg.database_url is None


def test_server_config_database_url_from_yaml(tmp_path):
    from superseded.server.config import ServerConfig

    key = tmp_path / "key.pem"
    key.write_text("x")
    config_file = tmp_path / "server.yaml"
    config_file.write_text(
        f"app_id: 222\nwebhook_secret: s\nprivate_key_path: {key}\n"
        f"database_url: postgresql://u:p@h/db\n"
    )
    cfg = ServerConfig.from_yaml(config_file)
    assert cfg.database_url == "postgresql://u:p@h/db"


def test_server_config_behind_proxy_defaults_false():
    config = ServerConfig()
    assert config.behind_proxy is False


def test_server_config_require_configured_rejects_non_loopback_without_tls():
    key_file = Path("/dev/null")
    config = ServerConfig(
        app_id=123,
        webhook_secret="s",
        private_key_path=key_file,
        host="0.0.0.0",
        behind_proxy=False,
    )
    with pytest.raises(ValueError, match="requires TLS"):
        config.require_configured()


def test_server_config_require_configured_allows_non_loopback_when_behind_proxy(caplog):
    key_file = Path("/dev/null")
    config = ServerConfig(
        app_id=123,
        webhook_secret="s",
        private_key_path=key_file,
        host="0.0.0.0",
        behind_proxy=True,
    )
    with caplog.at_level("WARNING", logger="superseded.server.config"):
        config.require_configured()
    assert any("SUPERSEDED_BEHIND_PROXY" in rec.message for rec in caplog.records)


def test_server_config_behind_proxy_from_env(monkeypatch, tmp_path):
    key = tmp_path / "key.pem"
    key.write_text("x")
    monkeypatch.setenv("SUPERSEDED_APP_ID", "111")
    monkeypatch.setenv("SUPERSEDED_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("SUPERSEDED_PRIVATE_KEY_PATH", str(key))
    monkeypatch.setenv("SUPERSEDED_BEHIND_PROXY", "true")
    cfg = ServerConfig.from_env()
    assert cfg.behind_proxy is True


def test_server_config_behind_proxy_falsey_from_env(monkeypatch, tmp_path):
    key = tmp_path / "key.pem"
    key.write_text("x")
    monkeypatch.setenv("SUPERSEDED_APP_ID", "111")
    monkeypatch.setenv("SUPERSEDED_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("SUPERSEDED_PRIVATE_KEY_PATH", str(key))
    monkeypatch.setenv("SUPERSEDED_BEHIND_PROXY", "0")
    cfg = ServerConfig.from_env()
    assert cfg.behind_proxy is False


def test_server_config_deepseek_key_defaults_none():
    """Sandbox fields were removed; verify the deepseek_api_key field exists instead."""
    config = ServerConfig()
    assert config.deepseek_api_key is None


def test_server_config_deepseek_api_key_from_env(monkeypatch, tmp_path):
    key_file = tmp_path / "key.pem"
    key_file.write_text("fake-private-key")
    monkeypatch.setenv("SUPERSEDED_APP_ID", "12345")
    monkeypatch.setenv("SUPERSEDED_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("SUPERSEDED_PRIVATE_KEY_PATH", str(key_file))
    monkeypatch.setenv("SUPERSEDED_DEEPSEEK_API_KEY", "dsk-test")

    config = ServerConfig.from_env()
    assert config.deepseek_api_key == "dsk-test"


def test_server_config_reasoning_effort_default_max():
    config = ServerConfig()
    assert config.reasoning_effort == "max"


def test_server_config_reasoning_effort_from_env(monkeypatch, tmp_path):
    key_file = tmp_path / "key.pem"
    key_file.write_text("fake-private-key")
    monkeypatch.setenv("SUPERSEDED_APP_ID", "12345")
    monkeypatch.setenv("SUPERSEDED_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("SUPERSEDED_PRIVATE_KEY_PATH", str(key_file))
    monkeypatch.setenv("SUPERSEDED_SERVER_REASONING_EFFORT", "low")

    config = ServerConfig.from_env()
    assert config.reasoning_effort == "low"


def test_server_config_provider_defaults_to_deepseek():
    config = ServerConfig()
    assert config.provider == "deepseek"


def test_server_config_reads_openai_and_anthropic_keys(monkeypatch):
    from superseded.server.config import ServerConfig

    _set_required_server_env(monkeypatch)
    monkeypatch.setenv("SUPERSEDED_OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("SUPERSEDED_ANTHROPIC_API_KEY", "sk-anthropic")
    cfg = ServerConfig.from_env()
    assert cfg.openai_api_key == "sk-openai"
    assert cfg.anthropic_api_key == "sk-anthropic"
