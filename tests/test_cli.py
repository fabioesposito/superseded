from __future__ import annotations

import tempfile
from pathlib import Path

from superseded.cli import init_command


def test_init_creates_directory_structure():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        init_command(repo)
        assert (repo / ".superseded").is_dir()
        assert (repo / ".superseded" / "config.yaml").is_file()
        assert (repo / ".superseded" / "rules.md").is_file()
        assert (repo / ".superseded" / "issues").is_dir()


def test_init_creates_default_config():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        init_command(repo)
        import yaml
        with open(repo / ".superseded" / "config.yaml") as f:
            config = yaml.safe_load(f)
        assert config["default_agent"] == "opencode"


def test_init_creates_rules_template():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        init_command(repo)
        rules = (repo / ".superseded" / "rules.md").read_text()
        assert "# Project Rules" in rules


def test_init_creates_example_ticket():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        init_command(repo)
        issues = list((repo / ".superseded" / "issues").glob("*.md"))
        assert len(issues) == 1
        content = issues[0].read_text()
        assert "title:" in content


def test_init_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        init_command(repo)
        init_command(repo)  # should not fail
        assert (repo / ".superseded" / "config.yaml").is_file()


def test_init_preserves_existing_config():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        config_dir = repo / ".superseded"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("port: 9000\n")
        init_command(repo)
        import yaml
        with open(config_dir / "config.yaml") as f:
            config = yaml.safe_load(f)
        assert config["port"] == 9000
