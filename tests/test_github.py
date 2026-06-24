from __future__ import annotations

from unittest.mock import patch

from superseded.server.github import GitHubApp


def test_sign_jwt_cached(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_file = tmp_path / "key.pem"
    key_file.write_bytes(pem)

    app = GitHubApp(app_id=123, private_key_path=key_file, webhook_secret="s")

    with patch("time.time", return_value=1000):
        jwt1 = app._sign_jwt()
        jwt2 = app._sign_jwt()
        assert jwt1 == jwt2

    with patch("time.time", return_value=1600):
        jwt3 = app._sign_jwt()
        assert jwt3 != jwt1
