import tempfile
from pathlib import Path

import yaml

from superseded.config import (
    RepoEntry,
    ResourceLimitsConfig,
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
    assert cfg.model == ""
    assert cfg.sandbox == "host"
    assert cfg.require_approval is False
    assert cfg.rtk is False


def test_stage_agent_config_custom():
    cfg = StageAgentConfig(
        cli="opencode", model="gpt-4o", sandbox="docker", require_approval=True, rtk=True
    )
    assert cfg.cli == "opencode"
    assert cfg.model == "gpt-4o"
    assert cfg.sandbox == "docker"
    assert cfg.require_approval is True
    assert cfg.rtk is True


def test_superseded_config_stages_default():
    cfg = SupersededConfig()
    assert cfg.stages == {}
    assert cfg.default_model == ""
    assert cfg.rtk is False


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
    assert cfg.rtk is False


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


def test_notifications_config_defaults():
    cfg = SupersededConfig()
    assert cfg.notifications.enabled is False
    assert cfg.notifications.ntfy_topic == ""
    assert cfg.notifications.slack.webhook_url == ""
    assert cfg.notifications.email.smtp_host == ""
    assert cfg.notifications.webhook.url == ""


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


def test_notifications_config_slack():
    cfg = SupersededConfig(
        notifications={
            "enabled": True,
            "slack": {"webhook_url": "https://hooks.slack.com/test"},
        }
    )
    assert cfg.notifications.slack.webhook_url == "https://hooks.slack.com/test"


def test_notifications_config_webhook():
    cfg = SupersededConfig(
        notifications={
            "enabled": True,
            "webhook": {
                "url": "https://example.com/hook",
                "headers": {"Authorization": "Bearer tok"},
            },
        }
    )
    assert cfg.notifications.webhook.url == "https://example.com/hook"
    assert cfg.notifications.webhook.headers == {"Authorization": "Bearer tok"}


def test_load_config_with_rtk():
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / ".superseded" / "config.yaml"
        write_yaml_config(config_path, {"rtk": True})
        config = load_config(Path(tmp))
        assert config.rtk is True


def test_verification_config_defaults():
    from superseded.config import VerificationConfig

    cfg = VerificationConfig()
    assert cfg.required_sections == []
    assert cfg.max_critical_findings == 0
    assert cfg.max_important_findings == 10


def test_stage_agent_config_with_verification():
    from superseded.config import VerificationConfig

    cfg = StageAgentConfig(
        cli="opencode",
        verify=VerificationConfig(required_sections=["Problem", "Solution"]),
    )
    assert cfg.verify.required_sections == ["Problem", "Solution"]


def test_config_stages_with_verification():
    from superseded.config import VerificationConfig

    cfg = SupersededConfig(
        stages={
            "spec": StageAgentConfig(
                cli="opencode",
                verify=VerificationConfig(
                    required_sections=["Problem", "Solution", "Requirements"],
                ),
            ),
            "review": StageAgentConfig(
                cli="opencode",
                verify=VerificationConfig(max_critical_findings=0, max_important_findings=3),
            ),
        }
    )
    assert cfg.stages["spec"].verify.required_sections == [
        "Problem",
        "Solution",
        "Requirements",
    ]
    assert cfg.stages["review"].verify.max_critical_findings == 0


def test_resource_limits_config_defaults():
    cfg = ResourceLimitsConfig()
    assert cfg.max_tokens == 0
    assert cfg.max_wall_time_seconds == 0
    assert cfg.max_cost_usd == 0.0


def test_stage_agent_config_with_resource_limits():
    cfg = StageAgentConfig(
        cli="opencode",
        resource_limits=ResourceLimitsConfig(max_tokens=500000, max_wall_time_seconds=1800),
    )
    assert cfg.resource_limits.max_tokens == 500000
    assert cfg.resource_limits.max_wall_time_seconds == 1800


def test_auto_advance_config_default():
    cfg = SupersededConfig()
    assert cfg.auto_advance is False


def test_auto_advance_config_enabled():
    cfg = SupersededConfig(auto_advance=True)
    assert cfg.auto_advance is True


def test_approvers_config_default():
    cfg = SupersededConfig()
    assert cfg.approvers == []


def test_approvers_config_set():
    cfg = SupersededConfig(approvers=["alice", "bob"])
    assert cfg.approvers == ["alice", "bob"]


def test_crg_config_defaults():
    from superseded.config import CRGConfig

    cfg = CRGConfig()
    assert cfg.enabled is False
    assert cfg.auto_build is True
    assert cfg.graph_stale_minutes == 60


def test_superseded_config_with_crg():
    from superseded.config import CRGConfig

    cfg = SupersededConfig(crg=CRGConfig(enabled=True))
    assert cfg.crg.enabled is True
