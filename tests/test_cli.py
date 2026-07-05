from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from superseded.cli import cli, format_memory_context, resolve_agent, resolve_model
from superseded.config import Config


def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


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


def test_resolve_agent_env_overrides_flag_and_config():
    with patch.dict("os.environ", {"SUPERSEDED_AGENT": "opencode"}, clear=False):
        assert resolve_agent(None, Config()) == "opencode"
        assert resolve_agent("codex", Config()) == "opencode"


def test_resolve_agent_flag_overrides_config_no_env():
    with patch.dict("os.environ", {}, clear=True):
        assert resolve_agent(None, Config()) == "opencode"
        assert resolve_agent("codex", Config()) == "codex"


def test_resolve_model_env_overrides():
    with patch.dict("os.environ", {"SUPERSEDED_MODEL": "gpt-5"}, clear=False):
        assert resolve_model(None, Config()) == "gpt-5"
    with patch.dict("os.environ", {}, clear=True):
        assert resolve_model(None, Config(model="cfg-model")) == "cfg-model"
        assert resolve_model("flag-model", Config()) == "flag-model"


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
    from superseded.cli import _persist_findings
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

    async def async_record(**kwargs):
        calls.append(kwargs)

    async def _dummy_aenter(self):
        return self

    async def _dummy_aexit(self, *exc):
        pass

    mock_store = type("FakeStore", (), {"__aenter__": _dummy_aenter, "__aexit__": _dummy_aexit})()
    mock_store.record_finding = staticmethod(async_record)

    import asyncio

    asyncio.run(_persist_findings(mock_store, result, "owner/repo"))
    assert len(calls) == 1
    assert calls[0]["reasoning"] == "suspicious input"


def test_run_review_exits_cleanly_when_agent_unavailable(tmp_path, monkeypatch, capsys):
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
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    from superseded.cli import _run_review

    with pytest.raises(SystemExit) as exc:
        _run_review(
            pr=None,
            diff_range="HEAD~1..HEAD",
            agent=None,
            model=None,
            output_format="json",
            post=False,
            passes=None,
        )
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "agent" in err.lower() or "path" in err.lower()


def test_run_review_honors_config_disabled_passes_when_flag_omitted(tmp_path, monkeypatch):
    """passes.style: false in .superseded.yaml must skip style when --passes is omitted."""
    (tmp_path / ".superseded.yaml").write_text("agent: claude-code\npasses:\n  style: false\n")
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
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/claude")

    invoked: list[str] = []

    def fake_run_pass(self, pass_name, prompt, timeout=300, progress=None, sess=None):
        invoked.append(pass_name)
        if progress is not None:
            progress(pass_name, "done")
        return []

    monkeypatch.setattr("superseded.review.engine.ReviewEngine.run_pass", fake_run_pass)

    from superseded.cli import _run_review

    _run_review(
        pr=None,
        diff_range="HEAD~1..HEAD",
        agent=None,
        model=None,
        output_format="json",
        post=False,
        passes=None,
    )

    assert "style" not in invoked
    assert {"security", "correctness", "performance", "architecture"} <= set(invoked)


def test_persist_and_link_batch_into_single_event_loop(monkeypatch):
    """_persist_findings and _link_comment_ids should each use a single asyncio.run()."""
    import asyncio

    from superseded.cli import _link_comment_ids, _persist_findings
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

    calls = []

    async def async_record(**kwargs):
        calls.append(kwargs)

    async def _dummy_aenter_b(self):
        return self

    async def _dummy_aexit_b(self, *exc):
        pass

    mock_store = type(
        "FakeStore",
        (),
        {"__aenter__": _dummy_aenter_b, "__aexit__": _dummy_aexit_b},
    )()
    mock_store.record_finding = staticmethod(async_record)
    mock_store.set_comment_id = AsyncMock()

    asyncio.run(_persist_findings(mock_store, result, "owner/repo"))
    persist_runs = len(run_calls)

    asyncio.run(_link_comment_ids(mock_store, result, [10, 20, 30]))
    total_runs = len(run_calls)

    assert persist_runs == 1, f"Expected 1 asyncio.run() for persist, got {persist_runs}"
    assert total_runs == persist_runs + 1, (
        f"Expected 1 asyncio.run() for link, got {total_runs - persist_runs}"
    )
    assert len(calls) == 3


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
    fake_engine.agent.is_available.return_value = True
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


def test_resolve_sandbox_env_overrides_flag(monkeypatch):
    from superseded.cli import resolve_sandbox

    with monkeypatch.context() as m:
        m.setenv("SUPERSEDED_SANDBOX", "0")
        assert resolve_sandbox(True, Config()) is False


def test_resolve_sandbox_env_truthy_overrides_flag(monkeypatch):
    from superseded.cli import resolve_sandbox

    with monkeypatch.context() as m:
        m.setenv("SUPERSEDED_SANDBOX", "1")
        assert resolve_sandbox(False, Config()) is True


def test_resolve_sandbox_flag_overrides_config():
    from superseded.cli import resolve_sandbox

    assert resolve_sandbox(True, Config(sandbox=False)) is True
    assert resolve_sandbox(False, Config(sandbox=True)) is False


def test_resolve_sandbox_defaults_to_config():
    from superseded.cli import resolve_sandbox

    assert resolve_sandbox(None, Config(sandbox=True)) is True
    assert resolve_sandbox(None, Config(sandbox=False)) is False


def test_resolve_sandbox_defaults_false():
    from superseded.cli import resolve_sandbox

    assert resolve_sandbox(None, Config()) is False


def test_run_review_sandbox_missing_sbx_exits(tmp_path, monkeypatch, capsys):
    """--sandbox with no sbx on PATH exits 2 with a clear sbx message."""
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
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/claude" if cmd != "sbx" else None)

    from superseded.cli import _run_review

    with pytest.raises(SystemExit) as exc:
        _run_review(
            pr=None,
            diff_range="HEAD~1..HEAD",
            agent=None,
            model=None,
            output_format="json",
            post=False,
            passes=None,
            sandbox=True,
        )
    assert exc.value.code == 2
    assert "sbx" in capsys.readouterr().err.lower()


def test_run_review_sandbox_builds_sandbox_executor(tmp_path, monkeypatch):
    """--sandbox with sbx present builds a SandboxExecutor and passes it to engine.review."""
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
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/sbx")

    captured: dict = {}

    def fake_review(self, **kwargs):
        captured.update(kwargs)
        from superseded.models import ReviewResult

        return ReviewResult(findings=[], warnings=[])

    monkeypatch.setattr("superseded.review.engine.ReviewEngine.review", fake_review)
    monkeypatch.setattr("superseded.review.engine.ReviewEngine.run_pass", lambda self, *a, **k: [])

    from superseded.cli import _run_review
    from superseded.review.executor import SandboxExecutor

    _run_review(
        pr=None,
        diff_range="HEAD~1..HEAD",
        agent=None,
        model=None,
        output_format="json",
        post=False,
        passes=None,
        sandbox=True,
    )
    ex = captured.get("executor")
    assert isinstance(ex, SandboxExecutor)


def test_serve_threads_smolvm_sandbox_fields(monkeypatch):
    """SandboxSettings built by `serve` carries kind+smolvm_image_* from ServerConfig."""
    import pathlib
    import tempfile

    pk = pathlib.Path(tempfile.mkstemp(suffix=".pem")[1])
    pk.write_text("dummy")
    monkeypatch.setenv("SUPERSEDED_APP_ID", "123456")
    monkeypatch.setenv("SUPERSEDED_WEBHOOK_SECRET", "whs")
    monkeypatch.setenv("SUPERSEDED_PRIVATE_KEY_PATH", str(pk))
    monkeypatch.setenv("SUPERSEDED_SANDBOX", "1")
    monkeypatch.setenv("SUPERSEDED_SANDBOX_KIND", "smolvm")
    monkeypatch.setenv("SUPERSEDED_SMOLVM_IMAGE", "gcr/x/all:1")

    captured = {}

    from superseded.server import worker as worker_mod

    real_init = worker_mod.ReviewWorker.__init__

    def spy(self, **kw):
        captured["sandbox"] = kw.get("sandbox")
        return real_init(self, **kw)

    monkeypatch.setattr(worker_mod.ReviewWorker, "__init__", spy)

    import contextlib

    import uvicorn  # noqa: F401

    monkeypatch.setattr("uvicorn.run", lambda **k: None)

    from click.testing import CliRunner

    from superseded.cli import cli

    runner = CliRunner()
    with contextlib.suppress(Exception):
        runner.invoke(cli, ["serve", "--host", "127.0.0.1", "--port", "0"])
    assert "sandbox" in captured
    assert captured["sandbox"].kind == "smolvm"
    assert captured["sandbox"].smolvm_image == "gcr/x/all:1"


def test_serve_refuses_no_sandbox_without_opt_in(monkeypatch, tmp_path):
    """serve must refuse to boot with the sandbox off unless explicitly opted in."""
    import pathlib

    pk = pathlib.Path(tmp_path / "key.pem")
    pk.write_text("dummy")
    monkeypatch.setenv("SUPERSEDED_APP_ID", "123456")
    monkeypatch.setenv("SUPERSEDED_WEBHOOK_SECRET", "whs")
    monkeypatch.setenv("SUPERSEDED_PRIVATE_KEY_PATH", str(pk))
    monkeypatch.setenv("SUPERSEDED_SANDBOX", "0")
    monkeypatch.delenv("SUPERSEDED_ALLOW_NO_SANDBOX", raising=False)

    from click.testing import CliRunner

    from superseded.cli import cli

    result = CliRunner().invoke(cli, ["serve", "--host", "127.0.0.1", "--port", "0"])
    assert result.exit_code == 2
    assert "refusing to serve without a sandbox" in result.output


def test_serve_allows_no_sandbox_with_explicit_opt_in(monkeypatch, tmp_path):
    import pathlib

    pk = pathlib.Path(tmp_path / "key.pem")
    pk.write_text("dummy")
    monkeypatch.setenv("SUPERSEDED_APP_ID", "123456")
    monkeypatch.setenv("SUPERSEDED_WEBHOOK_SECRET", "whs")
    monkeypatch.setenv("SUPERSEDED_PRIVATE_KEY_PATH", str(pk))
    monkeypatch.setenv("SUPERSEDED_SANDBOX", "0")
    monkeypatch.setenv("SUPERSEDED_ALLOW_NO_SANDBOX", "1")
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)

    from click.testing import CliRunner

    from superseded.cli import cli

    result = CliRunner().invoke(cli, ["serve", "--host", "127.0.0.1", "--port", "0"])
    assert "refusing to serve without a sandbox" not in result.output
    assert result.exit_code == 0
