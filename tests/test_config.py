import tempfile
from pathlib import Path

import yaml

from superseded.config import RepoEntry, StageAgentConfig, SupersededConfig, load_config


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


def test_config_repos_map():
    config = SupersededConfig(
        repo_path="/tmp/primary",
        repos={
            "frontend": RepoEntry(path="/tmp/frontend"),
            "backend": RepoEntry(path="/tmp/backend"),
        },
    )
    assert config.repos["frontend"].path == "/tmp/frontend"
    assert config.repos["backend"].path == "/tmp/backend"


def test_config_repos_empty_by_default():
    config = SupersededConfig(repo_path="/tmp/primary")
    assert config.repos == {}


def test_load_config_with_repos():
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / ".superseded" / "config.yaml"
        write_yaml_config(
            config_path,
            {
                "repo_path": tmp,
                "repos": {
                    "frontend": {"path": "/tmp/frontend"},
                    "backend": {"path": "/tmp/backend", "branch": "main"},
                },
            },
        )
        config = load_config(Path(tmp))
        assert config.repos["frontend"].path == "/tmp/frontend"
        assert config.repos["backend"].branch == "main"


def test_stage_agent_config_defaults():
    cfg = StageAgentConfig()
    assert cfg.cli == "claude-code"
    assert cfg.model == ""


def test_stage_agent_config_custom():
    cfg = StageAgentConfig(cli="opencode", model="gpt-4o")
    assert cfg.cli == "opencode"
    assert cfg.model == "gpt-4o"


def test_superseded_config_stages_default():
    cfg = SupersededConfig()
    assert cfg.stages == {}
    assert cfg.default_model == ""


def test_superseded_config_stages_populated():
    cfg = SupersededConfig(
        stages={
            "build": StageAgentConfig(cli="opencode", model="gpt-4o"),
        }
    )
    assert cfg.stages["build"].cli == "opencode"
    assert cfg.stages["build"].model == "gpt-4o"
