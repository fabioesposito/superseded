from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from superseded.cli import (
    cli,
    format_memory_context,
    resolve_model,
    resolve_provider,
    resolve_reasoning_effort,
)
from superseded.config import Config
from superseded.models import ReviewUsage


def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert version("superseded") in result.output


def test_serve_refuses_unconfigured_config(tmp_path):
    """serve must exit with error when config has no app_id/webhook_secret."""
    config_file = tmp_path / "server.yaml"
    config_file.write_text("port: 9999\n")  # no app_id, no webhook_secret
    runner = CliRunner()
    result = runner.invoke(cli, ["serve", "--config", str(config_file)])
    assert result.exit_code == 2
    assert "not configured" in result.output.lower() or "app_id" in result.output.lower()


@patch("superseded.cli._run_review")
def test_review_no_args_auto_detects(mock_review):
    mock_review.return_value = None
    runner = CliRunner()
    result = runner.invoke(cli, ["review"])
    assert result.exit_code == 0
    mock_review.assert_called_once()


def test_review_staged_flag_threads_to_fetch_diff(monkeypatch):
    captured: dict = {}

    def fake_run_review(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr("superseded.cli._run_review", fake_run_review)
    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--staged"])
    assert result.exit_code == 0
    assert captured.get("staged") is True


@patch("superseded.cli._run_review")
def test_review_staged_defaults_false(mock_review):
    mock_review.return_value = None
    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--pr", "5"])
    assert result.exit_code == 0
    assert mock_review.call_args.kwargs.get("staged") is False


@patch("superseded.cli._run_review")
def test_review_with_pr(mock_review):
    mock_review.return_value = None
    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--pr", "123"])
    assert result.exit_code == 0
    mock_review.assert_called_once()


def test_resolve_provider_flag_overrides_config_no_env():
    with patch.dict("os.environ", {}, clear=True):
        assert resolve_provider(None, Config()) == "deepseek"
        assert resolve_provider("other", Config()) == "other"


def test_resolve_provider_legacy_agent_env_alias():
    """SUPERSEDED_AGENT is accepted as a deprecated alias for SUPERSEDED_PROVIDER."""
    with patch.dict("os.environ", {"SUPERSEDED_AGENT": "deepseek"}, clear=False):
        assert resolve_provider(None, Config()) == "deepseek"


def test_resolve_provider_env_overrides_config():
    with patch.dict("os.environ", {"SUPERSEDED_PROVIDER": "other"}, clear=False):
        assert resolve_provider(None, Config()) == "other"


def test_resolve_provider_env_overrides_flag():
    """Env beats flag per documented precedence: env > flag > config."""
    with patch.dict("os.environ", {"SUPERSEDED_PROVIDER": "other"}, clear=False):
        assert resolve_provider("deepseek", Config()) == "other"


def test_resolve_provider_legacy_agent_warns_without_new_env():
    with (
        patch.dict("os.environ", {"SUPERSEDED_AGENT": "legacy-agent"}, clear=False),
        pytest.warns(DeprecationWarning, match="SUPERSEDED_AGENT is deprecated"),
    ):
        assert resolve_provider(None, Config()) == "legacy-agent"


def test_resolve_provider_new_env_wins_over_legacy():
    """When both env vars are set, SUPERSEDED_PROVIDER wins and no deprecation warning fires."""
    import warnings

    with (
        patch.dict(
            "os.environ",
            {"SUPERSEDED_AGENT": "legacy-agent", "SUPERSEDED_PROVIDER": "new-provider"},
            clear=False,
        ),
        warnings.catch_warnings(record=True) as recorded,
    ):
        warnings.simplefilter("always")
        assert resolve_provider(None, Config()) == "new-provider"
    assert not [w for w in recorded if issubclass(w.category, DeprecationWarning)]


def test_resolve_model_env_overrides():
    with patch.dict("os.environ", {"SUPERSEDED_MODEL": "gpt-5"}, clear=False):
        assert resolve_model(None, Config()) == "gpt-5"
    with patch.dict("os.environ", {}, clear=True):
        assert resolve_model(None, Config(model="cfg-model")) == "cfg-model"
        assert resolve_model("flag-model", Config()) == "flag-model"


def test_resolve_reasoning_effort_precedence(monkeypatch):
    monkeypatch.delenv("SUPERSEDED_REASONING_EFFORT", raising=False)
    # config fallback
    assert resolve_reasoning_effort(None, Config()) == "max"
    assert resolve_reasoning_effort(None, Config(reasoning_effort="low")) == "low"
    # flag beats config
    assert resolve_reasoning_effort("high", Config(reasoning_effort="low")) == "high"
    # env beats flag
    monkeypatch.setenv("SUPERSEDED_REASONING_EFFORT", "max")
    assert resolve_reasoning_effort("low", Config(reasoning_effort="high")) == "max"


def test_resolve_reasoning_effort_rejects_invalid_env(monkeypatch):
    """An invalid SUPERSEDED_REASONING_EFFORT exits 2 rather than silently no-op'ing effort."""
    monkeypatch.setenv("SUPERSEDED_REASONING_EFFORT", "bogus")
    with pytest.raises(SystemExit) as exc:
        resolve_reasoning_effort("high", Config())
    assert exc.value.code == 2


def test_format_memory_context_with_reasoning():
    dismissed = [
        {
            "pass": "performance",
            "title": "N+1 query",
            "reasoning": "Loops over 1000 rows; will hit DB N times per request.",
        }
    ]
    result = format_memory_context(dismissed)
    assert "N+1 query" in result
    assert "Loops over 1000 rows" in result
    assert "Rationale then was:" in result


def test_format_memory_context_without_reasoning():
    dismissed = [
        {
            "pass": "style",
            "title": "unclear naming",
            "reasoning": "",
        }
    ]
    result = format_memory_context(dismissed)
    assert "unclear naming" in result
    assert "Rationale then was:" not in result


def test_format_memory_context_truncates_long_reasoning():
    dismissed = [
        {
            "pass": "security",
            "title": "injection",
            "reasoning": "x" * 500,
        }
    ]
    result = format_memory_context(dismissed)
    assert len(result) < 600
    assert "\u2026" in result


def test_persist_findings_passes_reasoning(monkeypatch):
    from superseded.cli import _post_review_store
    from superseded.models import Finding, ReviewResult

    f = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        end_line=2,
        title="bad",
        description="d",
        suggestion="s",
        reasoning="suspicious input",
    )
    result = ReviewResult(findings=[f])

    calls = []

    async def async_record_batch(findings, repo):
        calls.extend(findings)

    async def _dummy_aenter(self):
        return self

    async def _dummy_aexit(self, *exc):
        pass

    mock_store = type("FakeStore", (), {"__aenter__": _dummy_aenter, "__aexit__": _dummy_aexit})()
    mock_store.record_findings_batch = async_record_batch

    import asyncio

    asyncio.run(_post_review_store(mock_store, result, "owner/repo", None, None, False, ""))
    assert len(calls) == 1
    assert calls[0]["reasoning"] == "suspicious input"


def test_run_review_exits_cleanly_when_provider_unknown(tmp_path, monkeypatch, capsys):
    """An unknown provider name must exit 2 with a clear error."""
    monkeypatch.setattr(
        "superseded.cli.fetch_diff",
        lambda pr=None, diff_range=None, files=None, staged=False: "diff --git a/x.py b/x.py\n",
    )
    monkeypatch.setattr("superseded.cli.fetch_pr_description", lambda pr: None)
    monkeypatch.setattr(
        "superseded.context.gathering.compute_file_context", lambda diff, root=None: None
    )
    monkeypatch.setattr("superseded.cli.repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        "superseded.context.gathering.run_static_analysis", lambda files, root: None
    )
    monkeypatch.setattr("superseded.context.gathering.retrieve_usages", lambda diff, root: None)
    monkeypatch.setattr("superseded.cli.current_repo", lambda: None)

    from superseded.cli import _run_review

    with pytest.raises(SystemExit) as exc:
        _run_review(
            pr=None,
            diff_range="HEAD~1..HEAD",
            provider="bogus",
            model=None,
            output_format="json",
            post=False,
            passes=None,
        )
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "provider" in err.lower()


def test_run_review_exits_partial_when_passes_warned(tmp_path, monkeypatch):
    """When some passes were skipped (warnings present), the CLI must exit with
    a distinct non-zero code so CI/scripts can tell infra degradation apart from
    a clean review."""
    monkeypatch.setattr(
        "superseded.cli.fetch_diff",
        lambda pr=None, diff_range=None, files=None, staged=False: "diff --git a/x.py b/x.py\n",
    )
    monkeypatch.setattr("superseded.cli.fetch_pr_description", lambda pr: None)
    monkeypatch.setattr(
        "superseded.context.gathering.compute_file_context", lambda diff, root=None: None
    )
    monkeypatch.setattr("superseded.cli.repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        "superseded.context.gathering.run_static_analysis", lambda files, root: None
    )
    monkeypatch.setattr("superseded.context.gathering.retrieve_usages", lambda diff, root: None)
    monkeypatch.setattr("superseded.cli.current_repo", lambda: None)
    monkeypatch.setenv("SUPERSEDED_DEEPSEEK_API_KEY", "test-key")

    from superseded.models import ReviewResult

    def fake_review(self, **kw):
        return ReviewResult(findings=[], warnings=["pass 'correctness' failed: no provider"])

    monkeypatch.setattr("superseded.review.engine.ReviewEngine.review", fake_review)
    monkeypatch.setattr(
        "superseded.review.engine.ReviewEngine.run_pass",
        lambda self, *a, **k: ([], ReviewUsage()),
    )

    from superseded.cli import EXIT_PARTIAL_FAILURE, _run_review

    with pytest.raises(SystemExit) as exc:
        _run_review(
            pr=None,
            diff_range="HEAD~1..HEAD",
            provider=None,
            model=None,
            output_format="json",
            post=False,
            passes=None,
        )
    assert exc.value.code == EXIT_PARTIAL_FAILURE


def test_run_review_clean_when_no_warnings(tmp_path, monkeypatch):
    """No warnings -> no SystemExit; the review completes normally (exit 0)."""
    monkeypatch.setattr(
        "superseded.cli.fetch_diff",
        lambda pr=None, diff_range=None, files=None, staged=False: "diff --git a/x.py b/x.py\n",
    )
    monkeypatch.setattr("superseded.cli.fetch_pr_description", lambda pr: None)
    monkeypatch.setattr(
        "superseded.context.gathering.compute_file_context", lambda diff, root=None: None
    )
    monkeypatch.setattr("superseded.cli.repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        "superseded.context.gathering.run_static_analysis", lambda files, root: None
    )
    monkeypatch.setattr("superseded.context.gathering.retrieve_usages", lambda diff, root: None)
    monkeypatch.setattr("superseded.cli.current_repo", lambda: None)
    monkeypatch.setenv("SUPERSEDED_DEEPSEEK_API_KEY", "test-key")

    from superseded.models import ReviewResult

    def fake_review(self, **kw):
        return ReviewResult(findings=[], warnings=[])

    monkeypatch.setattr("superseded.review.engine.ReviewEngine.review", fake_review)
    monkeypatch.setattr(
        "superseded.review.engine.ReviewEngine.run_pass",
        lambda self, *a, **k: ([], ReviewUsage()),
    )

    from superseded.cli import _run_review

    # Must NOT raise SystemExit.
    _run_review(
        pr=None,
        diff_range="HEAD~1..HEAD",
        provider=None,
        model=None,
        output_format="json",
        post=False,
        passes=None,
    )


def test_run_review_honors_config_disabled_passes_when_flag_omitted(tmp_path, monkeypatch):
    """passes.style: false in .superseded.yaml must skip style when --passes is omitted."""
    (tmp_path / ".superseded.yaml").write_text("provider: deepseek\npasses:\n  style: false\n")
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(
        "superseded.cli.fetch_diff",
        lambda pr=None, diff_range=None, files=None, staged=False: "diff --git a/x.py b/x.py\n",
    )
    monkeypatch.setattr("superseded.cli.fetch_pr_description", lambda pr: None)
    monkeypatch.setattr(
        "superseded.context.gathering.compute_file_context", lambda diff, root=None: None
    )
    monkeypatch.setattr("superseded.cli.repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        "superseded.context.gathering.run_static_analysis", lambda files, root: None
    )
    monkeypatch.setattr("superseded.context.gathering.retrieve_usages", lambda diff, root: None)
    monkeypatch.setattr("superseded.cli.current_repo", lambda: None)
    monkeypatch.setenv("SUPERSEDED_DEEPSEEK_API_KEY", "test-key")

    invoked: list[str] = []

    def fake_run_pass(self, pass_name, prompt, timeout=300, progress=None):
        invoked.append(pass_name)
        if progress is not None:
            progress(pass_name, "done")
        return [], ReviewUsage()

    monkeypatch.setattr("superseded.review.engine.ReviewEngine.run_pass", fake_run_pass)

    from superseded.cli import _run_review

    _run_review(
        pr=None,
        diff_range="HEAD~1..HEAD",
        provider=None,
        model=None,
        output_format="json",
        post=False,
        passes=None,
    )

    assert "style" not in invoked
    assert {"security", "correctness", "performance", "architecture"} <= set(invoked)


def test_persist_and_link_batch_into_single_event_loop(monkeypatch):
    """_post_review_store should persist and link in a single asyncio.run()."""
    import asyncio

    from superseded.cli import _post_review_store
    from superseded.models import Finding, ReviewResult

    findings = [
        Finding(
            pass_name="security",
            severity="critical",
            file=f"file{i}.py",
            line=1,
            end_line=2,
            title=f"issue {i}",
            description="d",
            suggestion="s",
        )
        for i in range(3)
    ]
    result = ReviewResult(findings=findings)

    run_calls = []
    original_run = asyncio.run

    def counting_run(coro):
        run_calls.append(1)
        return original_run(coro)

    monkeypatch.setattr("asyncio.run", counting_run)

    batch_calls = []

    async def async_record_batch(findings_list, repo):
        batch_calls.extend(findings_list)

    async def _dummy_aenter_b(self):
        return self

    async def _dummy_aexit_b(self, *exc):
        pass

    mock_store = type(
        "FakeStore",
        (),
        {"__aenter__": _dummy_aenter_b, "__aexit__": _dummy_aexit_b},
    )()
    mock_store.record_findings_batch = async_record_batch
    mock_store.set_comment_ids_batch = AsyncMock()

    asyncio.run(_post_review_store(mock_store, result, "owner/repo", None, None, False, ""))

    assert len(run_calls) == 1, f"Expected 1 asyncio.run(), got {len(run_calls)}"
    assert len(batch_calls) == 3


def test_resolve_graph_env_overrides_flag(monkeypatch):
    monkeypatch.setenv("SUPERSEDED_GRAPH", "false")
    from superseded.cli import resolve_graph
    from superseded.config import Config

    assert resolve_graph(True, Config()) is False


def test_resolve_graph_env_truthy_overrides_flag(monkeypatch):
    monkeypatch.setenv("SUPERSEDED_GRAPH", "1")
    from superseded.cli import resolve_graph
    from superseded.config import Config

    assert resolve_graph(False, Config()) is True


def test_resolve_graph_flag_overrides_config():
    from superseded.cli import resolve_graph
    from superseded.config import Config

    cfg = Config(graph=False)
    assert resolve_graph(True, cfg) is True
    cfg2 = Config(graph=True)
    assert resolve_graph(False, cfg2) is False


def test_resolve_graph_defaults_to_config():
    from superseded.cli import resolve_graph
    from superseded.config import Config

    assert resolve_graph(None, Config(graph=True)) is True
    assert resolve_graph(None, Config(graph=False)) is False


def test_resolve_graph_defaults_true():
    from superseded.cli import resolve_graph
    from superseded.config import Config

    assert resolve_graph(None, Config()) is True


def test_review_passes_graph_to_gather_context(monkeypatch):
    """`--graph`/`--no-graph` must propagate as the `graph` kwarg to
    `gather_context`."""
    from click.testing import CliRunner

    from superseded import cli as cli_mod
    from superseded.cli import cli

    captured = {}

    def fake_gather_context(diff, root, **kwargs):
        captured.update(kwargs)
        return {
            "file_context": None,
            "static_signals": None,
            "usage_signals": None,
            "conventions_signals": None,
            "spec_signals": None,
        }

    def fake_fetch_diff(*, pr, diff_range, files, staged=False):
        return "diff"

    def fake_engine_review(*a, **kw):
        from superseded.models import ReviewResult

        return ReviewResult(findings=[], warnings=[])

    monkeypatch.setattr(cli_mod, "gather_context", fake_gather_context)
    monkeypatch.setattr(cli_mod, "fetch_diff", fake_fetch_diff)
    monkeypatch.setattr(cli_mod, "repo_root", lambda: Path("/repo"))
    monkeypatch.setattr(cli_mod, "fetch_pr_description", lambda pr: None)
    fake_engine = MagicMock()
    fake_engine.review = fake_engine_review
    monkeypatch.setattr(
        cli_mod.ReviewEngine, "select", classmethod(lambda cls, *a, **kw: fake_engine)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["review", "--diff", "HEAD~1..HEAD", "--no-graph", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    assert captured.get("graph") is False

    captured.clear()
    result = runner.invoke(
        cli,
        ["review", "--diff", "HEAD~1..HEAD", "--graph", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    assert captured.get("graph") is True


def _fake_store_with_watermark(wm: str | None):
    store = MagicMock()

    async def _get(repo, pr):
        return wm

    store.get_watermark = _get
    return store


def test_resolve_no_watermark_uses_full_diff(monkeypatch):
    from superseded.cli import _resolve_pr_review_diff

    monkeypatch.setattr("superseded.cli.fetch_pr_head_sha", lambda pr: "head")
    monkeypatch.setattr("superseded.cli.fetch_diff", lambda **kw: "FULLDIFF")
    monkeypatch.setattr("superseded.cli.fetch_incremental_diff", lambda *a: ("", "ahead"))
    store = _fake_store_with_watermark(None)

    diff, mode, head = _resolve_pr_review_diff(pr=1, repo="o/r", store=store, full=False)
    assert diff == "FULLDIFF"
    assert mode == "full"
    assert head == "head"


def test_resolve_full_flag_skips_incremental(monkeypatch):
    from unittest.mock import MagicMock

    from superseded.cli import _resolve_pr_review_diff

    monkeypatch.setattr("superseded.cli.fetch_pr_head_sha", lambda pr: "head")
    monkeypatch.setattr("superseded.cli.fetch_diff", lambda **kw: "FULLDIFF")
    inc = MagicMock()
    monkeypatch.setattr("superseded.cli.fetch_incremental_diff", inc)
    store = _fake_store_with_watermark("base")

    diff, mode, _head = _resolve_pr_review_diff(pr=1, repo="o/r", store=store, full=True)
    assert diff == "FULLDIFF"
    assert mode == "full"
    inc.assert_not_called()


def test_resolve_ahead_uses_incremental(monkeypatch):
    from unittest.mock import MagicMock

    from superseded.cli import _resolve_pr_review_diff

    monkeypatch.setattr("superseded.cli.fetch_pr_head_sha", lambda pr: "head")
    full = MagicMock()
    monkeypatch.setattr("superseded.cli.fetch_diff", full)
    monkeypatch.setattr("superseded.cli.fetch_incremental_diff", lambda *a: ("INCDIFF", "ahead"))
    store = _fake_store_with_watermark("base")

    diff, mode, _head = _resolve_pr_review_diff(pr=1, repo="o/r", store=store, full=False)
    assert diff == "INCDIFF"
    assert mode == "incremental"
    full.assert_not_called()


def test_resolve_identical_returns_noop(monkeypatch):
    from superseded.cli import _resolve_pr_review_diff

    monkeypatch.setattr("superseded.cli.fetch_pr_head_sha", lambda pr: "head")
    monkeypatch.setattr("superseded.cli.fetch_diff", lambda **kw: "FULLDIFF")
    monkeypatch.setattr("superseded.cli.fetch_incremental_diff", lambda *a: (None, "identical"))
    store = _fake_store_with_watermark("base")

    diff, mode, _head = _resolve_pr_review_diff(pr=1, repo="o/r", store=store, full=False)
    assert diff is None
    assert mode == "noop"


def test_resolve_diverged_falls_back_to_full(monkeypatch):
    from superseded.cli import _resolve_pr_review_diff

    monkeypatch.setattr("superseded.cli.fetch_pr_head_sha", lambda pr: "head")
    monkeypatch.setattr("superseded.cli.fetch_diff", lambda **kw: "FULLDIFF")
    monkeypatch.setattr("superseded.cli.fetch_incremental_diff", lambda *a: (None, "diverged"))
    store = _fake_store_with_watermark("base")

    diff, mode, _head = _resolve_pr_review_diff(pr=1, repo="o/r", store=store, full=False)
    assert diff == "FULLDIFF"
    assert mode == "fallback"


def test_resolve_incremental_error_falls_back(monkeypatch):
    from superseded.cli import _resolve_pr_review_diff
    from superseded.incremental import IncrementalDiffError

    monkeypatch.setattr("superseded.cli.fetch_pr_head_sha", lambda pr: "head")
    monkeypatch.setattr("superseded.cli.fetch_diff", lambda **kw: "FULLDIFF")

    def _boom(*a):
        raise IncrementalDiffError("nope")

    monkeypatch.setattr("superseded.cli.fetch_incremental_diff", _boom)
    store = _fake_store_with_watermark("base")

    diff, mode, _head = _resolve_pr_review_diff(pr=1, repo="o/r", store=store, full=False)
    assert diff == "FULLDIFF"
    assert mode == "fallback"


@patch("superseded.cli.setup_logging")
@patch("superseded.cli._run_review")
def test_review_calls_setup_logging(mock_review, mock_setup, monkeypatch):
    mock_review.return_value = None
    monkeypatch.delenv("SUPERSEDED_LOG_FORMAT", raising=False)
    monkeypatch.delenv("SUPERSEDED_LOG_LEVEL", raising=False)
    runner = CliRunner()
    result = runner.invoke(cli, ["--log-format", "json", "review", "--pr", "1"])
    assert result.exit_code == 0
    mock_setup.assert_called()
    called_fmt = mock_setup.call_args.args[0]
    assert called_fmt == "json"


@patch("superseded.cli.setup_logging")
def test_log_format_env_overrides_flag(mock_setup, monkeypatch):
    monkeypatch.setenv("SUPERSEDED_LOG_FORMAT", "json")
    runner = CliRunner()
    runner.invoke(cli, ["--log-format", "text", "feedback", "--rules"])
    called_fmt = mock_setup.call_args.args[0]
    assert called_fmt == "json"


@patch("superseded.cli.setup_logging")
def test_log_level_passed_through(mock_setup, monkeypatch):
    monkeypatch.delenv("SUPERSEDED_LOG_LEVEL", raising=False)
    runner = CliRunner()
    runner.invoke(cli, ["--log-level", "DEBUG", "feedback", "--rules"])
    called_level = mock_setup.call_args.args[1]
    assert called_level == "DEBUG"


@patch("superseded.cli.setup_logging")
def test_verbose_env_forces_debug(mock_setup, monkeypatch):
    monkeypatch.delenv("SUPERSEDED_LOG_LEVEL", raising=False)
    monkeypatch.setenv("VERBOSE", "1")
    runner = CliRunner()
    runner.invoke(cli, ["--log-level", "WARNING", "feedback", "--rules"])
    assert mock_setup.call_args.args[1] == "DEBUG"


@patch("superseded.cli.setup_logging")
def test_verbose_env_overrides_log_level_env(mock_setup, monkeypatch):
    monkeypatch.setenv("SUPERSEDED_LOG_LEVEL", "INFO")
    monkeypatch.setenv("VERBOSE", "true")
    runner = CliRunner()
    runner.invoke(cli, ["feedback", "--rules"])
    assert mock_setup.call_args.args[1] == "DEBUG"


@patch("superseded.cli.setup_logging")
def test_verbose_env_falsy_does_not_force_debug(mock_setup, monkeypatch):
    monkeypatch.setenv("VERBOSE", "0")
    runner = CliRunner()
    runner.invoke(cli, ["--log-level", "INFO", "feedback", "--rules"])
    assert mock_setup.call_args.args[1] == "INFO"


@patch("superseded.cli.setup_logging")
@patch("superseded.cli._run_review")
def test_log_format_config_file_used_when_no_flag_or_env(
    mock_review, mock_setup, tmp_path, monkeypatch
):
    mock_review.return_value = None
    monkeypatch.delenv("SUPERSEDED_LOG_FORMAT", raising=False)
    monkeypatch.delenv("SUPERSEDED_LOG_LEVEL", raising=False)
    (tmp_path / ".superseded.yaml").write_text("log_format: json\nlog_level: INFO\n")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--pr", "1"])
    assert result.exit_code == 0
    assert mock_setup.call_args.args[0] == "json"
    assert mock_setup.call_args.args[1] == "INFO"


def test_serve_refuses_without_deepseek_key(monkeypatch, tmp_path):
    """serve must exit 2 if SUPERSEDED_DEEPSEEK_API_KEY is not set."""
    import pathlib

    pk = pathlib.Path(tmp_path / "key.pem")
    pk.write_text("dummy")
    monkeypatch.setenv("SUPERSEDED_APP_ID", "123456")
    monkeypatch.setenv("SUPERSEDED_WEBHOOK_SECRET", "whs")
    monkeypatch.setenv("SUPERSEDED_PRIVATE_KEY_PATH", str(pk))
    monkeypatch.delenv("SUPERSEDED_DEEPSEEK_API_KEY", raising=False)

    from click.testing import CliRunner

    from superseded.cli import cli

    result = CliRunner().invoke(cli, ["serve", "--host", "127.0.0.1", "--port", "0"])
    assert result.exit_code == 2
    assert "SUPERSEDED_DEEPSEEK_API_KEY" in result.output


def test_verify_flag_passed_to_run_review(monkeypatch):
    """--no-verify flag is parsed and passed through to _run_review."""
    captured: dict = {}

    def fake_run_review(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr("superseded.cli._run_review", fake_run_review)

    from click.testing import CliRunner

    from superseded.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--no-verify", "--diff", "HEAD~1..HEAD"])
    assert result.exit_code == 0
    assert captured.get("verify") is False

    captured.clear()
    result = runner.invoke(cli, ["review", "--verify", "--diff", "HEAD~1..HEAD"])
    assert result.exit_code == 0
    assert captured.get("verify") is True

    captured.clear()
    result = runner.invoke(cli, ["review", "--diff", "HEAD~1..HEAD"])
    assert result.exit_code == 0
    assert captured.get("verify") is None


def test_cli_provider_choices_include_all_providers():
    from click.testing import CliRunner

    from superseded.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--help"])
    assert result.exit_code == 0
    assert "deepseek, openai, anthropic" in result.output
    assert "medium" in result.output  # widened effort choice
