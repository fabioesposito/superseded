from __future__ import annotations

import os
import tempfile
from pathlib import Path

from superseded.config import Config, load_config


def test_default_config():
    cfg = Config()
    assert cfg.agent == "opencode"
    assert cfg.model == "deepseek-v4-pro"
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


def test_config_defaults():
    c = Config()
    assert c.static_analysis is True
    assert c.usage_retrieval is True


def test_load_config_with_enrichment_flags(tmp_path):
    cfg = tmp_path / ".superseded.yaml"
    cfg.write_text("static_analysis: false\nusage_retrieval: false\n")
    c = load_config(cfg)
    assert c.static_analysis is False
    assert c.usage_retrieval is False


def test_conventions_default_true():
    from superseded.config import Config

    assert Config().conventions is True
    assert Config().spec_retrieval is True


def test_conventions_can_be_disabled():
    from superseded.config import Config

    cfg = Config(conventions=False, spec_retrieval=False)
    assert cfg.conventions is False
    assert cfg.spec_retrieval is False


def test_write_config_round_trip(tmp_path):
    from superseded.config import Config, load_config, write_config

    cfg = Config(agent="claude-code", model="claude-sonnet-4-6")
    target = tmp_path / ".superseded.yaml"
    write_config(cfg, target)

    loaded = load_config(target)
    assert loaded.agent == "claude-code"
    assert loaded.model == "claude-sonnet-4-6"
    assert loaded.passes.security is True
    assert loaded.passes.architecture is True
    assert loaded.format == "table"
    assert loaded.memory is True


def test_write_config_no_temp_lingering(tmp_path):
    from superseded.config import write_config

    target = tmp_path / ".superseded.yaml"
    write_config(Config(), target)
    temps = list(tmp_path.glob("*.tmp"))
    assert temps == []


def test_write_config_contains_nested_passes_block(tmp_path):
    from superseded.config import write_config

    target = tmp_path / ".superseded.yaml"
    write_config(Config(), target)
    text = target.read_text()
    assert "\npasses:" in text or text.startswith("passes:")
    assert "security:" in text


def test_write_config_default_path(tmp_path, monkeypatch):
    from superseded.config import Config, load_config, write_config

    monkeypatch.chdir(tmp_path)
    write_config(Config(agent="codex"))
    assert (tmp_path / ".superseded.yaml").exists()
    loaded = load_config(None)
    assert loaded.agent == "codex"


def test_config_graph_default_true():
    from superseded.config import Config

    assert Config().graph is True


def test_config_graph_round_trip(tmp_path):
    from superseded.config import Config, load_config, write_config

    cfg = Config(graph=False)
    target = tmp_path / ".superseded.yaml"
    write_config(cfg, target)
    loaded = load_config(target)
    assert loaded.graph is False


def test_config_progressive_defaults_true():
    from superseded.config import Config

    cfg = Config()
    assert cfg.progressive is True


def test_config_progressive_can_be_disabled():
    from superseded.config import Config

    cfg = Config(progressive=False)
    assert cfg.progressive is False
