from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

from superseded.context.static_analysis import (
    STATIC_BUDGET,
    RuffTool,
    run_static_analysis,
)


def test_ruff_detect_true(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    assert RuffTool().detect(tmp_path) is True


def test_ruff_detect_false(tmp_path):
    assert RuffTool().detect(tmp_path) is False


def test_ruff_build_command():
    tool = RuffTool()
    cmd = tool.build_command(["a.py", "b.py"], Path("/repo"))
    assert cmd[0] == "ruff"
    assert "check" in cmd
    assert "--output-format=concise" in cmd
    assert "a.py" in cmd
    assert "b.py" in cmd


def test_ruff_parse_output():
    tool = RuffTool()
    out = "a.py:1:1: F401 unused\n"
    result = tool.parse_output(out, "", Path("/repo"))
    assert "F401" in result


def test_budget_truncation(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    tool_output = "x" * (STATIC_BUDGET + 500)
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: MagicMock(returncode=1, stdout=tool_output, stderr=""),
    )
    result = run_static_analysis(["a.py"], tmp_path, {"python"})
    assert "omitted by static-analysis budget" in result
    assert len(result) <= STATIC_BUDGET + 200


def test_missing_binary_skipped(tmp_path, monkeypatch, caplog):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")

    def fail(*a, **kw):
        raise FileNotFoundError("no such binary")

    monkeypatch.setattr("subprocess.run", fail)
    with caplog.at_level("WARNING"):
        result = run_static_analysis(["a.py"], tmp_path, {"python"})
    assert result is None
    assert "not on PATH" in caplog.text


def test_timeout_skipped(tmp_path, monkeypatch, caplog):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")

    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="ruff", timeout=30)

    monkeypatch.setattr("subprocess.run", timeout)
    with caplog.at_level("WARNING"):
        result = run_static_analysis(["a.py"], tmp_path, {"python"})
    assert result is None
    assert "timed out" in caplog.text


def test_nonzero_exit_still_parses(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: MagicMock(returncode=1, stdout="err:1:1: bad\n", stderr=""),
    )
    result = run_static_analysis(["a.py"], tmp_path, {"python"})
    assert result is not None
    assert "bad" in result


def test_no_tools_detected_returns_none():
    result = run_static_analysis(["a.py"], Path("/nonexistent"), {"rust"})
    assert result is None
