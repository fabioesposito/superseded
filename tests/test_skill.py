from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from superseded.cli import cli
from superseded.skill import SKILL_AGENTS, build_skill_text, install_skill, skill_dir_for


def test_build_skill_text_has_frontmatter():
    text = build_skill_text()
    assert text.startswith("---\n")
    assert "name: superseded" in text
    assert "description:" in text
    # anti-rationalization + invocation must be present
    assert "Do not probe PATH" in text or "Do not verify superseded is installed" in text
    assert "superseded review --pr" in text


def test_skill_dir_for_each_agent():
    for name in SKILL_AGENTS:
        d = skill_dir_for(name)
        assert d.name == "superseded"
        assert d.parent.name == "skills"
    assert skill_dir_for("claude-code").parts[-3] == ".claude"
    assert skill_dir_for("opencode").parts[-3] == "opencode"
    assert skill_dir_for("codex").parts[-3] == ".agents"


def _patch_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))


def test_install_writes_all_three(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    written, skipped = install_skill(list(SKILL_AGENTS))
    assert skipped == []
    assert set(written) == set(SKILL_AGENTS)
    for name in SKILL_AGENTS:
        f = skill_dir_for(name) / "SKILL.md"
        assert f.exists()
        assert f.read_text() == build_skill_text()


def test_install_specific_agent(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    written, _ = install_skill(["claude-code"])
    assert written == ["claude-code"]
    assert (skill_dir_for("claude-code") / "SKILL.md").exists()
    assert not skill_dir_for("opencode").exists()
    assert not skill_dir_for("codex").exists()


def test_install_identical_is_noop(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    install_skill(["codex"])  # seed
    f = skill_dir_for("codex") / "SKILL.md"
    mtime_before = f.stat().st_mtime_ns
    written, skipped = install_skill(["codex"])  # re-run, identical, no force
    assert skipped == []
    assert written == ["codex"]
    assert f.read_text() == build_skill_text()
    assert f.stat().st_mtime_ns == mtime_before  # not rewritten


def test_install_refuses_diff_without_force(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    f = skill_dir_for("codex") / "SKILL.md"
    f.parent.mkdir(parents=True)
    f.write_text("different content")
    written, skipped = install_skill(["codex"])
    assert written == []
    assert skipped == ["codex"]
    assert f.read_text() == "different content"  # unchanged


def test_install_force_overwrites_diff(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    f = skill_dir_for("codex") / "SKILL.md"
    f.parent.mkdir(parents=True)
    f.write_text("different content")
    written, skipped = install_skill(["codex"], force=True)
    assert skipped == []
    assert written == ["codex"]
    assert f.read_text() == build_skill_text()


def test_install_atomic_no_tmp_lingering(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    install_skill(["opencode"])
    d = skill_dir_for("opencode")
    assert list(d.glob("*.tmp")) == []


def test_cli_skill_install_happy_path(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["skill", "install"])
    assert result.exit_code == 0, result.output
    for name in SKILL_AGENTS:
        assert (skill_dir_for(name) / "SKILL.md").read_text() == build_skill_text()


def test_cli_skill_print_emits_skill(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["skill", "print"])
    assert result.exit_code == 0, result.output
    assert result.output == build_skill_text() + "\n"
    # print performs no writes
    for name in SKILL_AGENTS:
        assert not skill_dir_for(name).exists()


def test_cli_skill_install_unknown_agent(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["skill", "install", "--agent", "bogus"])
    assert result.exit_code == 2
    assert "bogus" in result.output


def test_cli_skill_install_force_flag(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    f = skill_dir_for("codex") / "SKILL.md"
    f.parent.mkdir(parents=True)
    f.write_text("old")
    runner = CliRunner()
    no_force = runner.invoke(cli, ["skill", "install", "--agent", "codex"])
    assert no_force.exit_code == 2
    assert f.read_text() == "old"
    forced = runner.invoke(cli, ["skill", "install", "--agent", "codex", "--force"])
    assert forced.exit_code == 0, forced.output
    assert f.read_text() == build_skill_text()
