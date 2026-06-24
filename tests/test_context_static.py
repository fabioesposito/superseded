from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from superseded.context.static_analysis import (
    STATIC_BUDGET,
    TOOLS,
    GitleaksTool,
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
    result = tool.parse_output(out, "", Path("/repo"), ["a.py"])
    assert "F401" in result


def test_gitleaks_parse_output_filters_to_changed_files():
    stdout = json.dumps(
        [
            {"Description": "aws key", "StartLine": 1, "File": "a.py"},
            {"Description": "github token", "StartLine": 5, "File": "b.py"},
        ]
    )
    out = GitleaksTool().parse_output(stdout, "", Path("/repo"), ["a.py"])
    assert "a.py" in out
    assert "aws key" in out
    assert "b.py" not in out
    assert "github token" not in out


def test_gitleaks_detect_on_git_dir(tmp_path):
    (tmp_path / ".git").mkdir()
    assert GitleaksTool().detect(tmp_path) is True
    bare = tmp_path / "bare"
    bare.mkdir()
    assert GitleaksTool().detect(bare) is False


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


def test_gitleaks_parse_output_invalid_json_returns_empty():
    tool = GitleaksTool()
    result = tool.parse_output("not valid json", "", Path("/repo"), ["a.py"])
    assert result == ""


def test_tools_sorted_alphabetically():
    names = [t.name for t in TOOLS]
    assert names == sorted(names)


def test_budget_truncation_per_finding_not_per_tool():
    huge_output = "x" * (STATIC_BUDGET + 100)

    mock_tool = MagicMock()
    mock_tool.name = "mock"
    mock_tool.languages = ["*"]
    mock_tool.detect.return_value = True
    mock_tool.build_command.return_value = ["echo"]
    mock_tool.parse_output.return_value = huge_output

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = huge_output
    mock_result.stderr = ""

    with (
        patch("superseded.context.static_analysis.subprocess.run", return_value=mock_result),
        patch("superseded.context.static_analysis.TOOLS", [mock_tool]),
    ):
        result = run_static_analysis(["a.py"], Path("/tmp"))

    assert result is not None
    assert "omitted" in result.lower()
