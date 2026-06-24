from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from superseded.cli import cli


@patch("superseded.cli.fetch_diff")
@patch("superseded.cli.ReviewEngine")
def test_full_review_flow(mock_engine_cls, mock_fetch):
    mock_fetch.return_value = "diff --git a/a.py b/a.py\n+new line"
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    mock_engine.review.return_value = MagicMock(findings=[], summary={})

    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--pr", "123", "--agent", "claude-code"])

    assert result.exit_code == 0
    mock_fetch.assert_called_once_with(pr=123, diff_range=None)
    mock_engine.review.assert_called_once()
