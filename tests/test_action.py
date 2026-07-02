from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_action_is_composite():
    action = yaml.safe_load((REPO_ROOT / "action.yml").read_text())
    assert action["runs"]["using"] == "composite"
    assert "docker" not in action["runs"]
    assert "image" not in action["runs"]


def test_action_inputs_are_server_based():
    action = yaml.safe_load((REPO_ROOT / "action.yml").read_text())
    inputs = action["inputs"]
    assert "server-url" in inputs
    assert "server-key" in inputs
    # The old agent-credentials inputs are gone.
    assert "agent" not in inputs
    assert "model" not in inputs
    assert "anthropic_api_key" not in inputs
    assert "openai_api_key" not in inputs


def test_action_posts_to_review_pr():
    text = (REPO_ROOT / "action.yml").read_text()
    assert "/review/pr" in text
    assert "SUPERSEDED_SERVER_URL" in text
    assert "SUPERSEDED_SERVER_KEY" in text


def test_entrypoint_sh_removed():
    assert not (REPO_ROOT / "docker" / "entrypoint.sh").exists()


def test_dockerfile_no_longer_copies_entrypoint():
    dockerfile = (REPO_ROOT / "docker" / "Dockerfile").read_text()
    assert "entrypoint.sh" not in dockerfile
