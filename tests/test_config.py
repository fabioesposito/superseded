from __future__ import annotations

import os
import tempfile
from pathlib import Path

from superseded.config import Config, load_config


def test_default_config():
    cfg = Config()
    assert cfg.agent == "claude-code"
    assert cfg.model is None
    assert cfg.passes.security is True
    assert cfg.post_to_pr is False
    assert cfg.format == "table"
    assert cfg.memory is True


def test_load_config_from_yaml():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("agent: opencode\nmodel: gpt-4o\npasses:\n  security: false\n  style: false\n")
        f.flush()
        cfg = load_config(Path(f.name))
        assert cfg.agent == "opencode"
        assert cfg.passes.security is False
    os.unlink(f.name)


def test_config_passes_override():
    cfg = Config()
    assert cfg.is_pass_enabled("security") is True
    assert cfg.is_pass_enabled("nonexistent") is False
