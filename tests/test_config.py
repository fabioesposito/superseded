import tempfile
from pathlib import Path

import yaml

from superseded.config import (
    RepoEntry,
    StageAgentConfig,
    SupersededConfig,
    load_config,
)


def write_yaml_config(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data))


def test_default_config():
    config = SupersededConfig()
    assert config.default_agent == "opencode"
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
        assert config.default_agent == "opencode"


def test_load_config_partial_override():
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / ".superseded" / "config.yaml"
        write_yaml_config(config_path, {"port": 9000})
        config = load_config(Path(tmp))
        assert config.port == 9000
        assert config.default_agent == "opencode"


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
    assert cfg.cli == "opencode"
    assert cfg.model == "opencode-go/kimi-k2.5"


def test_stage_agent_config_custom():
    cfg = StageAgentConfig(cli="opencode", model="gpt-4o")
    assert cfg.cli == "opencode"
    assert cfg.model == "gpt-4o"


def test_superseded_config_stages_default():
    cfg = SupersededConfig()
    assert cfg.stages == {}
    assert cfg.default_model == "opencode-go/kimi-k2.5"


def test_superseded_config_stages_populated():
    cfg = SupersededConfig(
        stages={
            "build": StageAgentConfig(cli="opencode", model="gpt-4o"),
        }
    )
    assert cfg.stages["build"].cli == "opencode"
    assert cfg.stages["build"].model == "gpt-4o"


def test_config_api_keys_default_empty():
    cfg = SupersededConfig()
    assert cfg.openai_api_key == ""
    assert cfg.anthropic_api_key == ""
    assert cfg.opencode_api_key == ""
    assert cfg.source_code_root == ""


def test_config_api_keys_from_file():
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / ".superseded" / "config.yaml"
        write_yaml_config(
            config_path,
            {
                "openai_api_key": "sk-test-123",
                "anthropic_api_key": "sk-ant-test-456",
                "opencode_api_key": "oc-test-789",
            },
        )
        config = load_config(Path(tmp))
        assert config.openai_api_key == "sk-test-123"
        assert config.anthropic_api_key == "sk-ant-test-456"
        assert config.opencode_api_key == "oc-test-789"


def test_config_env_var_overrides(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("OPENAI_API_KEY", "env-openai")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-anthropic")
        monkeypatch.setenv("OPENCODE_API_KEY", "env-opencode")
        config = load_config(Path(tmp))
        assert config.openai_api_key == "env-openai"
        assert config.anthropic_api_key == "env-anthropic"
        assert config.opencode_api_key == "env-opencode"


def test_config_source_code_root_from_file():
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / ".superseded" / "config.yaml"
        write_yaml_config(config_path, {"source_code_root": "/opt/repos"})
        config = load_config(Path(tmp))
        assert config.source_code_root == "/opt/repos"


def test_notifications_config_defaults():
    cfg = SupersededConfig()
    assert cfg.notifications.enabled is False
    assert cfg.notifications.ntfy_topic == ""


def test_notifications_config_from_file():
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / ".superseded" / "config.yaml"
        write_yaml_config(
            config_path,
            {
                "notifications": {
                    "enabled": True,
                    "ntfy_topic": "my-project",
                },
            },
        )
        config = load_config(Path(tmp))
        assert config.notifications.enabled is True
        assert config.notifications.ntfy_topic == "my-project"
