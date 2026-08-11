from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from superseded.config import Config, load_config


def test_default_config():
    cfg = Config()
    assert cfg.provider == "deepseek"
    assert cfg.model is None
    assert cfg.passes.security is True
    assert cfg.post_to_pr is False
    assert cfg.format == "table"
    assert cfg.memory is True


def test_load_config_from_yaml():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("provider: deepseek\nmodel: gpt-4o\npasses:\n  security: false\n  style: false\n")
        f.flush()
        cfg = load_config(Path(f.name))
        assert cfg.provider == "deepseek"
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

    cfg = Config(provider="deepseek", model="gpt-4o")
    target = tmp_path / ".superseded.yaml"
    write_config(cfg, target)

    loaded = load_config(target)
    assert loaded.provider == "deepseek"
    assert loaded.model == "gpt-4o"
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
    write_config(Config(provider="deepseek"))
    assert (tmp_path / ".superseded.yaml").exists()
    loaded = load_config(None)
    assert loaded.provider == "deepseek"


def test_config_verify_defaults_to_true():
    from superseded.config import Config

    assert Config().verify is True


def test_config_verify_from_dict():
    from superseded.config import Config

    config = Config(verify=False)
    assert config.verify is False


def test_config_graph_default_true():
    from superseded.config import Config

    assert Config().graph is True


def test_config_sandbox_field_removed():
    from superseded.config import Config

    cfg = Config()
    assert not hasattr(cfg, "sandbox")


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


def test_config_log_defaults():
    from superseded.config import Config

    cfg = Config()
    assert cfg.log_format == "text"
    assert cfg.log_level == "WARNING"


def test_config_log_round_trips(tmp_path):
    from superseded.config import Config, load_config, write_config

    cfg = Config(log_format="json", log_level="INFO")
    path = tmp_path / ".superseded.yaml"
    write_config(cfg, path)
    loaded = load_config(path)
    assert loaded.log_format == "json"
    assert loaded.log_level == "INFO"


def test_load_config_hard_errors_on_legacy_cli_agent(tmp_path):
    """A YAML with `agent: opencode` is a hard error post-v0.6."""
    from superseded.config import load_config

    cfg_path = tmp_path / ".superseded.yaml"
    cfg_path.write_text("agent: opencode\n")
    with pytest.raises(ValueError, match=r"CLI agents were removed in v0.6.0"):
        load_config(cfg_path)


def test_load_config_hard_errors_on_legacy_claude_code(tmp_path):
    from superseded.config import load_config

    cfg_path = tmp_path / ".superseded.yaml"
    cfg_path.write_text("agent: claude-code\n")
    with pytest.raises(ValueError, match="CLI agents were removed"):
        load_config(cfg_path)


def test_load_config_hard_errors_on_legacy_codex(tmp_path):
    from superseded.config import load_config

    cfg_path = tmp_path / ".superseded.yaml"
    cfg_path.write_text("agent: codex\n")
    with pytest.raises(ValueError, match="CLI agents were removed"):
        load_config(cfg_path)


def test_load_config_legacy_agent_with_unknown_value_treats_as_provider(tmp_path):
    """If `agent:` has a value that isn't a known CLI agent, treat it as `provider:` and warn."""
    import warnings

    from superseded.config import load_config

    cfg_path = tmp_path / ".superseded.yaml"
    cfg_path.write_text("agent: openai\n")  # not a known CLI agent
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = load_config(cfg_path)
    assert cfg.provider == "openai"
    assert any("agent:" in str(w.message) for w in caught)


def test_load_config_ignores_legacy_sandbox_key(tmp_path):
    """A YAML with `sandbox: true` is silently ignored (with a warning)."""
    import warnings

    from superseded.config import load_config

    cfg_path = tmp_path / ".superseded.yaml"
    cfg_path.write_text("provider: deepseek\nsandbox: true\n")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = load_config(cfg_path)
    assert cfg.provider == "deepseek"
    assert not hasattr(cfg, "sandbox")
    assert any("sandbox:" in str(w.message) for w in caught)
