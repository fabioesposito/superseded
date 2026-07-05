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


def test_server_config_sandbox_defaults():
    config = ServerConfig()
    assert config.sandbox_enabled is True
    assert config.sbx_binary == "sbx"
    assert config.sandbox_timeout == 600
    assert config.sandbox_keep_on_error is False
    assert config.sandbox_io_mode == "exec"


def test_server_config_sandbox_from_env(monkeypatch, tmp_path):
    key_file = tmp_path / "key.pem"
    key_file.write_text("fake-private-key")
    monkeypatch.setenv("SUPERSEDED_APP_ID", "12345")
    monkeypatch.setenv("SUPERSEDED_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("SUPERSEDED_PRIVATE_KEY_PATH", str(key_file))
    monkeypatch.setenv("SUPERSEDED_SANDBOX", "0")
    monkeypatch.setenv("SUPERSEDED_SANDBOX_TIMEOUT", "900")
    monkeypatch.setenv("SUPERSEDED_SANDBOX_IO_MODE", "cp")

    config = ServerConfig.from_env()
    assert config.sandbox_enabled is False
    assert config.sandbox_timeout == 900
    assert config.sandbox_io_mode == "cp"


def test_from_env_sandbox_kind_smolvm(monkeypatch):
    _set_required_server_env(monkeypatch)
    monkeypatch.setenv("SUPERSEDED_SANDBOX_KIND", "smolvm")
    cfg = ServerConfig.from_env()
    assert cfg.sandbox_kind == "smolvm"


def test_from_env_sandbox_kind_default_is_sbx(monkeypatch):
    _set_required_server_env(monkeypatch)
    cfg = ServerConfig.from_env()
    assert cfg.sandbox_kind == "sbx"


def test_from_env_smolvm_binary(monkeypatch):
    _set_required_server_env(monkeypatch)
    monkeypatch.setenv("SUPERSEDED_SMOLVM_BINARY", "/opt/smolvm/bin/smol")
    cfg = ServerConfig.from_env()
    assert cfg.smolvm_binary == "/opt/smolvm/bin/smol"


def test_from_env_smolvm_images_per_agent(monkeypatch):
    _set_required_server_env(monkeypatch)
    monkeypatch.setenv("SUPERSEDED_SMOLVM_IMAGE_CLAUDE", "gcr/x/c:1")
    monkeypatch.setenv("SUPERSEDED_SMOLVM_IMAGE_OPENCODE", "gcr/x/o:1")
    monkeypatch.setenv("SUPERSEDED_SMOLVM_IMAGE_CODEX", "gcr/x/d:1")
    cfg = ServerConfig.from_env()
    assert cfg.smolvm_image_claude == "gcr/x/c:1"
    assert cfg.smolvm_image_opencode == "gcr/x/o:1"
    assert cfg.smolvm_image_codex == "gcr/x/d:1"


def test_from_env_smolvm_image_host_wide(monkeypatch):
    _set_required_server_env(monkeypatch)
    monkeypatch.setenv("SUPERSEDED_SMOLVM_IMAGE", "gcr/x/all:1")
    cfg = ServerConfig.from_env()
    assert cfg.smolvm_image == "gcr/x/all:1"
