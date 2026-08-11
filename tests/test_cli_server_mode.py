from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from superseded.cli import cli
from superseded.models import Finding, ReviewResult


def _ok_result():
    return ReviewResult(
        findings=[
            Finding(
                pass_name="security",
                severity="critical",
                file="a.py",
                line=1,
                title="T",
                description="D",
                suggestion="S",
            )
        ]
    )


def test_review_server_mode_requires_pr():
    runner = CliRunner()
    with patch("superseded.cli.review_via_server") as mock_rev:
        result = runner.invoke(
            cli,
            ["review", "--server", "https://srv", "--server-key", "sk"],
        )
    assert result.exit_code == 2
    assert "--pr" in result.output
    mock_rev.assert_not_called()


def test_review_server_mode_rejects_diff_combo():
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "review",
            "--server",
            "https://srv",
            "--server-key",
            "sk",
            "--pr",
            "1",
            "--diff",
            "HEAD~1..HEAD",
        ],
    )
    assert result.exit_code == 2
    assert "cannot be combined" in result.output or "diff" in result.output.lower()


def test_review_server_mode_renders_table(monkeypatch):
    monkeypatch.setenv("SUPERSEDED_SERVER_URL", "https://srv")
    monkeypatch.setenv("SUPERSEDED_SERVER_KEY", "sk")
    monkeypatch.setattr("superseded.cli.current_repo", lambda: "octocat/hello-world")
    runner = CliRunner()
    with patch("superseded.cli.review_via_server", return_value=_ok_result()) as mock_rev:
        result = runner.invoke(cli, ["review", "--pr", "7"])
    assert result.exit_code == 0
    assert "a.py" in result.output
    mock_rev.assert_called_once()
    kwargs = mock_rev.call_args.kwargs
    assert kwargs["server_url"] == "https://srv"
    assert kwargs["server_key"] == "sk"
    assert kwargs["owner"] == "octocat"
    assert kwargs["repo"] == "hello-world"
    assert kwargs["pr_number"] == 7


def test_review_server_mode_json():
    runner = CliRunner()
    with (
        patch("superseded.cli.current_repo", lambda: "octocat/hello-world"),
        patch("superseded.cli.review_via_server", return_value=_ok_result()),
    ):
        result = runner.invoke(
            cli,
            [
                "review",
                "--server",
                "https://srv",
                "--server-key",
                "sk",
                "--pr",
                "7",
                "--format",
                "json",
            ],
        )
    assert result.exit_code == 0
    assert '"findings"' in result.output
    assert "a.py" in result.output


def test_review_server_mode_no_post_passes_post_false():
    runner = CliRunner()
    with (
        patch("superseded.cli.current_repo", lambda: "octocat/hello-world"),
        patch("superseded.cli.review_via_server", return_value=_ok_result()) as mock_rev,
    ):
        result = runner.invoke(
            cli,
            ["review", "--server", "https://srv", "--server-key", "sk", "--pr", "7", "--no-post"],
        )
    assert result.exit_code == 0
    assert mock_rev.call_args.kwargs["post"] is False


def test_review_server_mode_post_flag_warns():
    runner = CliRunner()
    with (
        patch("superseded.cli.current_repo", lambda: "octocat/hello-world"),
        patch("superseded.cli.review_via_server", return_value=_ok_result()),
    ):
        result = runner.invoke(
            cli,
            ["review", "--server", "https://srv", "--server-key", "sk", "--pr", "7", "--post"],
        )
    assert result.exit_code == 0
    assert "--post" in result.output or "post" in result.output.lower()


def test_review_server_mode_missing_key_exit_2(monkeypatch):
    monkeypatch.delenv("SUPERSEDED_SERVER_KEY", raising=False)
    runner = CliRunner()
    with patch("superseded.cli.current_repo", lambda: "octocat/hello-world"):
        result = runner.invoke(
            cli,
            ["review", "--server", "https://srv", "--pr", "7"],
        )
    assert result.exit_code == 2


def test_review_server_mode_no_remote_no_owner_exit_2():
    runner = CliRunner()
    with patch("superseded.cli.current_repo", lambda: None):
        result = runner.invoke(
            cli,
            ["review", "--server", "https://srv", "--server-key", "sk", "--pr", "7"],
        )
    assert result.exit_code == 2


def test_review_server_mode_server_error_exit_code(monkeypatch):
    from superseded.server.client import ServerReviewError

    monkeypatch.setenv("SUPERSEDED_SERVER_URL", "https://srv")
    monkeypatch.setenv("SUPERSEDED_SERVER_KEY", "sk")
    runner = CliRunner()
    with (
        patch("superseded.cli.current_repo", lambda: "octocat/hello-world"),
        patch(
            "superseded.cli.review_via_server",
            side_effect=ServerReviewError("nope", exit_code=2),
        ),
    ):
        result = runner.invoke(cli, ["review", "--pr", "7"])
    assert result.exit_code == 2


def test_review_server_mode_warnings_exit_3():
    result_with_warnings = ReviewResult(
        findings=[],
        warnings=["security pass skipped"],
    )
    runner = CliRunner()
    with (
        patch("superseded.cli.current_repo", lambda: "octocat/hello-world"),
        patch("superseded.cli.review_via_server", return_value=result_with_warnings),
    ):
        result = runner.invoke(
            cli,
            ["review", "--server", "https://srv", "--server-key", "sk", "--pr", "7"],
        )
    assert result.exit_code == 3


def test_review_server_mode_warns_ignored_flags():
    runner = CliRunner()
    with (
        patch("superseded.cli.current_repo", lambda: "octocat/hello-world"),
        patch("superseded.cli.review_via_server", return_value=_ok_result()),
    ):
        result = runner.invoke(
            cli,
            [
                "review",
                "--server",
                "https://srv",
                "--server-key",
                "sk",
                "--pr",
                "7",
                "--full",
                "--no-memory",
                "--verify",
            ],
        )
    assert result.exit_code == 0
    assert "--full" in result.output
    assert "--no-memory" in result.output
    assert "--provider" not in result.output
    assert "--model" not in result.output


def test_review_server_mode_config_sourced_warns(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERSEDED_SERVER_URL", raising=False)
    monkeypatch.delenv("SUPERSEDED_SERVER_KEY", raising=False)
    cfg = tmp_path / ".superseded.yaml"
    cfg.write_text("server: https://cfg.example.com\nserver_key: cfgkey\n")
    runner = CliRunner()
    with (
        patch("superseded.cli.current_repo", lambda: "octocat/hello-world"),
        patch("superseded.cli.review_via_server", return_value=_ok_result()) as mock_rev,
    ):
        result = runner.invoke(cli, ["review", "--config", str(cfg), "--pr", "7"])
    assert result.exit_code == 0
    assert mock_rev.call_args.kwargs["server_url"] == "https://cfg.example.com"
    assert "server-mode enabled by 'server:'" in result.output


def test_review_server_mode_flag_overrides_env(monkeypatch):
    monkeypatch.setenv("SUPERSEDED_SERVER_URL", "https://env.example.com")
    monkeypatch.setenv("SUPERSEDED_SERVER_KEY", "envkey")
    runner = CliRunner()
    with (
        patch("superseded.cli.current_repo", lambda: "octocat/hello-world"),
        patch("superseded.cli.review_via_server", return_value=_ok_result()) as mock_rev,
    ):
        result = runner.invoke(
            cli,
            ["review", "--server", "https://flag.example.com", "--pr", "7"],
        )
    assert result.exit_code == 0
    assert mock_rev.call_args.kwargs["server_url"] == "https://flag.example.com"
    assert "server-mode enabled by" not in result.output
