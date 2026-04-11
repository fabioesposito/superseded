import tempfile
from pathlib import Path

import yaml

from superseded.config import SupersededConfig, load_config


def write_yaml_config(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data))


def test_default_config():
    config = SupersededConfig()
    assert config.default_agent == "claude-code"
    assert config.stage_timeout_seconds == 600
    assert config.port == 8000


def test_load_config_from_file():
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / ".superseded" / "config.yaml"
        write_yaml_config(
            config_path,
            {
                "default_agent": "opencode",
                "stage_timeout_seconds": 300,
                "repo_path": tmp,
            },
        )
        config = load_config(Path(tmp))
        assert config.default_agent == "opencode"
        assert config.stage_timeout_seconds == 300
        assert config.repo_path == tmp


def test_load_config_missing_file_uses_defaults():
    with tempfile.TemporaryDirectory() as tmp:
        config = load_config(Path(tmp))
        assert config.default_agent == "claude-code"


def test_load_config_partial_override():
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / ".superseded" / "config.yaml"
        write_yaml_config(config_path, {"port": 9000})
        config = load_config(Path(tmp))
        assert config.port == 9000
        assert config.default_agent == "claude-code"
