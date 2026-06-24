from __future__ import annotations

import hashlib
import hmac

import pytest

from superseded.server.github import GitHubApp


@pytest.fixture
def app(tmp_path):
    key_file = tmp_path / "private.pem"
    key_file.write_text("fake-key")
    return GitHubApp(
        app_id=12345,
        private_key_path=key_file,
        webhook_secret="whsec_test_secret",
    )


def test_verify_webhook_valid(app):
    payload = b'{"action":"opened"}'
    signature = "sha256=" + hmac.new(b"whsec_test_secret", payload, hashlib.sha256).hexdigest()
    assert app.verify_webhook(payload, signature) is True


def test_verify_webhook_invalid(app):
    payload = b'{"action":"opened"}'
    signature = "sha256=" + "0" * 64
    assert app.verify_webhook(payload, signature) is False


def test_verify_webhook_timing_safe(app):
    payload = b'{"action":"opened"}'
    sig1 = "sha256=" + hmac.new(b"whsec_test_secret", payload, hashlib.sha256).hexdigest()
    sig2 = "sha256=" + "a" * 64
    app.verify_webhook(payload, sig1)
    app.verify_webhook(payload, sig2)


def test_verify_webhook_missing_prefix(app):
    payload = b'{"action":"opened"}'
    assert app.verify_webhook(payload, "invalid_format") is False


def test_verify_webhook_empty_signature(app):
    assert app.verify_webhook(b"payload", "") is False
