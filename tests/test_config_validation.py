from __future__ import annotations

import tempfile
from pathlib import Path

from superseded.config import SupersededConfig, load_config, validate_config


def test_validate_config_empty_agent():
    config = SupersededConfig(default_agent="")
    errors = validate_config(config)
    assert any("default_agent" in e for e in errors)


def test_validate_config_unknown_agent():
    config = SupersededConfig(default_agent="nonexistent")
    errors = validate_config(config)
    assert any("nonexistent" in e for e in errors)


def test_validate_config_valid():
    config = SupersededConfig(default_agent="opencode")
    errors = validate_config(config)
    assert errors == []


def test_validate_config_invalid_timeout():
    config = SupersededConfig(stage_timeout_seconds=-1)
    errors = validate_config(config)
    assert any("timeout" in e.lower() for e in errors)


def test_validate_config_invalid_port():
    config = SupersededConfig(port=99999)
    errors = validate_config(config)
    assert any("port" in e.lower() for e in errors)


def test_load_config_returns_validation_errors():
    with tempfile.TemporaryDirectory() as tmp:
        config = load_config(Path(tmp))
        errors = validate_config(config)
        assert errors == []
