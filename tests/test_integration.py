from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import aiosqlite
from click.testing import CliRunner

from superseded.cli import _run_review, cli
from superseded.config import Config
from superseded.models import Finding, ReviewResult

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS review_stats (
    repo         TEXT    NOT NULL,
    pass         TEXT    NOT NULL,
    severity     TEXT    NOT NULL,
    file_pattern TEXT    NOT NULL DEFAULT '*',
    total        INTEGER NOT NULL DEFAULT 0,
    accepted     INTEGER NOT NULL DEFAULT 0,
    dismissed    INTEGER NOT NULL DEFAULT 0,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (repo, pass, severity, file_pattern)
);
CREATE TABLE IF NOT EXISTS learned_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    repo            TEXT    NOT NULL,
    rule_text       TEXT    NOT NULL,
    evidence_count  INTEGER NOT NULL DEFAULT 0,
    confidence      REAL    NOT NULL DEFAULT 1.0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_applied_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS reflection_state (
    repo               TEXT    NOT NULL,
    last_feedback_id   INTEGER NOT NULL,
    last_reflection_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (repo)
);
CREATE TABLE IF NOT EXISTS findings (
    id          TEXT PRIMARY KEY,
    repo        TEXT    NOT NULL,
    pass        TEXT    NOT NULL,
    severity    TEXT    NOT NULL,
    file        TEXT    NOT NULL,
    line        INTEGER,
    title       TEXT    NOT NULL,
    description TEXT    NOT NULL,
    reasoning   TEXT    DEFAULT ''
);
CREATE TABLE IF NOT EXISTS feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id TEXT    NOT NULL REFERENCES findings(id),
    action     TEXT    NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class FakeStore:
    def __init__(self):
        self.findings = {}
        self.comment_ids = {}
        self.feedback = []
        self._dismissed = set()
        self.dismissed_calls = 0
        self.watermarks = {}
        self.set_watermark_calls = []
        self._learned_rules: list[dict] = []
        self._reflection_state: dict[str, int] = {}
        self._db_conn: aiosqlite.Connection | None = None

    @asynccontextmanager
    async def _db(self):
        if self._db_conn is None:
            self._db_conn = await aiosqlite.connect(":memory:")
            await self._db_conn.executescript(_SCHEMA)
        yield self._db_conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def init(self):
        pass

    async def get_watermark(self, repo, pr_number):
        return self.watermarks.get((repo, pr_number))

    async def set_watermark(self, repo, pr_number, head_sha):
        self.watermarks[(repo, pr_number)] = head_sha
        self.set_watermark_calls.append((repo, pr_number, head_sha))

    async def get_dismissed_findings(self, repo):
        self.dismissed_calls += 1
        return [self.findings[fid] for fid in self._dismissed if self.findings[fid]["repo"] == repo]

    async def record_finding(
        self,
        finding_id,
        repo,
        pass_name,
        severity,
        file,
        line,
        title,
        description,
        reasoning="",
    ):
        self.findings[finding_id] = {
            "id": finding_id,
            "repo": repo,
            "pass": pass_name,
            "severity": severity,
            "file": file,
            "line": line,
            "title": title,
            "description": description,
            "reasoning": reasoning,
        }

    async def record_findings_batch(self, findings, repo):
        for f in findings:
            await self.record_finding(
                finding_id=f["id"],
                repo=repo,
                pass_name=f["pass_name"],
                severity=f["severity"],
                file=f["file"],
                line=f["line"],
                title=f["title"],
                description=f["description"],
                reasoning=f.get("reasoning", ""),
            )

    async def set_comment_id(self, finding_id, comment_id):
        self.comment_ids[comment_id] = finding_id
        if finding_id in self.findings:
            self.findings[finding_id]["comment_id"] = comment_id

    async def set_comment_ids_batch(self, pairs):
        for finding_id, comment_id in pairs:
            await self.set_comment_id(finding_id, comment_id)

    async def record_feedback_by_comment_id(self, comment_id, action):
        fid = self.comment_ids.get(comment_id)
        if fid is None:
            return False
        self.feedback.append((fid, action))
        if action == "dismiss":
            self._dismissed.add(fid)
        return True

    async def get_learned_rules(self, repo, limit=5):
        return [
            r
            for r in self._learned_rules
            if r.get("repo", "") == repo and r.get("confidence", 1.0) >= 0.3
        ][:limit]

    async def get_reflection_state(self, repo):
        return self._reflection_state.get(repo, 0)

    async def set_reflection_state(self, repo, last_feedback_id):
        self._reflection_state[repo] = last_feedback_id

    async def refresh_review_stats(self, repo):
        pass

    async def get_review_stats(self, repo, min_sample):
        return []

    async def prune_stale_rules(self, repo, max_age_days=30):
        return 0

    async def dismiss_learned_rule(self, rule_id):
        return True

    async def reinforce_learned_rule(self, rule_id):
        return True

    async def get_all_learned_rules(self, repo):
        return self._learned_rules

    async def get_installation_config(self, installation_id):
        return {}

    async def set_installation_config(self, installation_id, key, value):
        pass


def _make_finding():
    return Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        end_line=2,
        title="t",
        description="d",
        suggestion="s",
    )


@patch("superseded.cli._resolve_pr_review_diff")
@patch("superseded.cli.post_review_to_pr")
@patch("superseded.cli.MemoryStore")
@patch("superseded.cli.current_repo")
@patch("superseded.cli.ReviewEngine")
@patch("superseded.context.gathering.compute_file_context")
@patch("superseded.cli.fetch_pr_description")
@patch("superseded.cli.fetch_diff")
def test_review_wires_memory_persistence_and_comment_ids(
    mock_fetch,
    mock_desc,
    mock_ctx,
    mock_engine_cls,
    mock_repo,
    mock_store_cls,
    mock_post,
    mock_resolve,
):
    mock_fetch.return_value = "diff"
    mock_resolve.return_value = ("diff", "full", None)
    mock_ctx.return_value = "ctx"
    mock_desc.return_value = "PR body"
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    finding = _make_finding()
    mock_engine.review.return_value = ReviewResult(findings=[finding])
    mock_repo.return_value = "owner/repo"
    store = FakeStore()
    mock_store_cls.return_value = store
    mock_post.return_value = [9001]

    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--pr", "123", "--post"])

    assert result.exit_code == 0, result.output
    assert mock_engine.review.call_count == 1
    kwargs = mock_engine.review.call_args.kwargs
    assert kwargs.get("pr_description") == "PR body"
    assert kwargs.get("file_context") == "ctx"
    assert store.dismissed_calls >= 1
    assert finding.id in store.findings
    assert store.comment_ids.get(9001) == finding.id
    mock_post.assert_called_once()


@patch("superseded.cli.load_config")
@patch("superseded.cli._resolve_pr_review_diff")
@patch("superseded.cli.post_review_to_pr")
@patch("superseded.cli.MemoryStore")
@patch("superseded.cli.current_repo")
@patch("superseded.cli.ReviewEngine")
@patch("superseded.cli._build_learned_context")
@patch("superseded.cli.gather_context")
@patch("superseded.cli.fetch_pr_description")
@patch("superseded.cli.fetch_diff")
def test_review_post_to_pr_in_config_posts_without_flag(
    mock_fetch,
    mock_desc,
    mock_gather,
    mock_learned,
    mock_engine_cls,
    mock_repo,
    mock_store_cls,
    mock_post,
    mock_resolve,
    mock_load_config,
):
    """post_to_pr: true in the config file posts even without the --post flag."""
    mock_load_config.return_value = Config(post_to_pr=True)
    mock_fetch.return_value = "diff"
    mock_resolve.return_value = ("diff", "full", None)
    mock_gather.return_value = {
        "file_context": "ctx",
        "static_signals": None,
        "usage_signals": None,
        "conventions_signals": None,
        "spec_signals": None,
    }
    mock_learned.return_value = None
    mock_desc.return_value = "PR body"
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    mock_engine.review.return_value = ReviewResult(findings=[_make_finding()])
    mock_repo.return_value = "owner/repo"
    mock_store_cls.return_value = FakeStore()
    mock_post.return_value = [9001]

    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--pr", "123"])

    assert result.exit_code == 0, result.output
    mock_post.assert_called_once()


@patch("superseded.cli._resolve_pr_review_diff")
@patch("superseded.cli.MemoryStore")
@patch("superseded.cli.current_repo")
@patch("superseded.cli.ReviewEngine")
@patch("superseded.cli.fetch_diff")
def test_review_injects_dismissed_findings_as_memory(
    mock_fetch, mock_engine_cls, mock_repo, mock_store_cls, mock_resolve
):
    mock_fetch.return_value = "diff"
    mock_resolve.return_value = ("diff", "full", None)
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    mock_engine.review.return_value = ReviewResult(findings=[])
    mock_repo.return_value = "owner/repo"
    store = FakeStore()
    store.findings["security-old"] = {
        "id": "security-old",
        "repo": "owner/repo",
        "pass": "security",
        "severity": "suggestion",
        "file": "old.py",
        "line": 9,
        "title": "Missing type hints",
        "description": "dismissed before",
    }
    store.comment_ids[5] = "security-old"
    store._dismissed.add("security-old")
    mock_store_cls.return_value = store

    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--pr", "1"])

    assert result.exit_code == 0, result.output
    kwargs = mock_engine.review.call_args.kwargs
    mem = kwargs.get("memory_context")
    assert mem and "Missing type hints" in mem


@patch("superseded.cli.fetch_diff")
@patch("superseded.cli.ReviewEngine")
@patch("superseded.context.gathering.compute_file_context")
@patch("superseded.cli.fetch_pr_description")
def test_review_passes_context_args_even_without_memory(
    mock_desc, mock_ctx, mock_engine_cls, mock_fetch
):
    mock_fetch.return_value = "diff"
    mock_ctx.return_value = "ctx"
    mock_desc.return_value = "PR body"
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    mock_engine.review.return_value = ReviewResult(findings=[])

    with patch("superseded.cli.current_repo", return_value=None):
        runner = CliRunner()
        result = runner.invoke(cli, ["review", "--pr", "5"])

    assert result.exit_code == 0, result.output
    kwargs = mock_engine.review.call_args.kwargs
    assert kwargs.get("file_context") == "ctx"
    assert kwargs.get("pr_description") == "PR body"


@patch("superseded.cli.check_pr_feedback")
def test_feedback_check_records_reactions_as_dismiss(mock_check):
    store = FakeStore()
    finding = _make_finding()
    asyncio.run(
        store.record_finding(finding.id, "owner/repo", "security", "critical", "a.py", 1, "t", "d")
    )
    asyncio.run(store.set_comment_id(finding.id, 42))
    mock_check.return_value = [
        {"id": 42, "path": "a.py", "line": 1, "reactions": {"+1": 0, "-1": 2}, "resolved": False},
        {"id": 99, "path": "x.py", "line": 1, "reactions": {"+1": 3, "-1": 0}, "resolved": False},
    ]

    with (
        patch("superseded.cli.MemoryStore", return_value=store),
        patch("superseded.cli.current_repo", return_value="owner/repo"),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["feedback", "--check", "--pr", "1"])

    assert result.exit_code == 0, result.output
    assert (finding.id, "dismiss") in store.feedback


@patch("superseded.cli.check_pr_feedback")
def test_feedback_check_records_resolved_as_dismiss(mock_check):
    store = FakeStore()
    finding = _make_finding()
    asyncio.run(
        store.record_finding(finding.id, "owner/repo", "security", "critical", "a.py", 1, "t", "d")
    )
    asyncio.run(store.set_comment_id(finding.id, 7))
    mock_check.return_value = [{"id": 7, "resolved": True, "reactions": {"+1": 0, "-1": 0}}]

    with (
        patch("superseded.cli.MemoryStore", return_value=store),
        patch("superseded.cli.current_repo", return_value="owner/repo"),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["feedback", "--check", "--pr", "1"])

    assert result.exit_code == 0, result.output
    assert (finding.id, "dismiss") in store.feedback


def test_feedback_manual_dismiss_resolves_comment_id():
    store = FakeStore()
    finding = _make_finding()
    asyncio.run(
        store.record_finding(finding.id, "owner/repo", "security", "critical", "a.py", 1, "t", "d")
    )
    asyncio.run(store.set_comment_id(finding.id, 555))

    with patch("superseded.cli.MemoryStore", return_value=store):
        runner = CliRunner()
        result = runner.invoke(cli, ["feedback", "555", "--dismiss"])

    assert result.exit_code == 0, result.output
    assert (finding.id, "dismiss") in store.feedback


def test_feedback_manual_unknown_comment_id_reports_error():
    store = FakeStore()
    with patch("superseded.cli.MemoryStore", return_value=store):
        runner = CliRunner()
        result = runner.invoke(cli, ["feedback", "999", "--dismiss"])
    assert result.exit_code != 0
    assert "999" in result.output


def test_context_enrichment_called(monkeypatch):
    """Verify run_static_analysis and retrieve_usages are called and kwargs forwarded."""
    monkeypatch.setattr(
        "superseded.cli.fetch_diff",
        lambda pr=None, diff_range=None, files=None, staged=False: "diff --git a/x.py b/x.py\n+x",
    )
    monkeypatch.setattr("superseded.cli.fetch_pr_description", lambda pr: None)
    monkeypatch.setattr(
        "superseded.context.gathering.compute_file_context", lambda d, root=None: None
    )
    monkeypatch.setattr("superseded.cli.current_repo", lambda: None)

    called_static = []
    called_usage = []

    def fake_static(changed_files, root):
        called_static.append(True)
        return "static output"

    def fake_usage(diff, root):
        called_usage.append(True)
        return "usage output"

    monkeypatch.setattr("superseded.context.gathering.run_static_analysis", fake_static)
    monkeypatch.setattr("superseded.context.gathering.retrieve_usages", fake_usage)

    mock_engine = MagicMock()
    mock_engine.review.return_value = ReviewResult(findings=[])
    monkeypatch.setattr("superseded.cli.ReviewEngine.select", lambda *a, **kw: mock_engine)

    _run_review(
        pr=None,
        diff_range="HEAD~1..HEAD",
        agent=None,
        model=None,
        output_format="json",
        post=False,
        passes=None,
        graph=False,
    )

    assert called_static
    assert called_usage
    call_kwargs = mock_engine.review.call_args
    assert call_kwargs[1].get("static_signals") == "static output"
    assert call_kwargs[1].get("usage_signals") == "usage output"


def test_context_disabled_skips_enrichment(monkeypatch):
    """When config disables enrichment, functions are not called."""
    monkeypatch.setattr(
        "superseded.cli.fetch_diff",
        lambda pr=None, diff_range=None, files=None, staged=False: "diff --git a/x.py b/x.py\n+x",
    )
    monkeypatch.setattr("superseded.cli.fetch_pr_description", lambda pr: None)
    monkeypatch.setattr(
        "superseded.context.gathering.compute_file_context", lambda d, root=None: None
    )
    monkeypatch.setattr("superseded.cli.current_repo", lambda: None)

    called = []
    monkeypatch.setattr(
        "superseded.context.gathering.run_static_analysis",
        lambda *a, **kw: (called.append("static"), None)[1],
    )
    monkeypatch.setattr(
        "superseded.context.gathering.retrieve_usages",
        lambda *a, **kw: (called.append("usage"), None)[1],
    )

    mock_engine = MagicMock()
    mock_engine.review.return_value = ReviewResult(findings=[])
    monkeypatch.setattr("superseded.cli.ReviewEngine.select", lambda *a, **kw: mock_engine)

    from superseded.config import Config

    monkeypatch.setattr(
        "superseded.cli.load_config",
        lambda path=None: Config(static_analysis=False, usage_retrieval=False),
    )

    _run_review(
        pr=None,
        diff_range="HEAD~1..HEAD",
        agent=None,
        model=None,
        output_format="json",
        post=False,
        passes=None,
    )

    assert not called


def test_conventions_and_specs_called_and_forwarded(monkeypatch):
    monkeypatch.setattr(
        "superseded.cli.fetch_diff",
        lambda pr=None, diff_range=None, files=None, staged=False: "diff --git a/x.py b/x.py\n+x",
    )
    monkeypatch.setattr("superseded.cli.fetch_pr_description", lambda pr: None)
    monkeypatch.setattr(
        "superseded.context.gathering.compute_file_context", lambda d, root=None: None
    )
    monkeypatch.setattr("superseded.cli.current_repo", lambda: None)
    monkeypatch.setattr("superseded.context.gathering.run_static_analysis", lambda *a, **kw: None)
    monkeypatch.setattr("superseded.context.gathering.retrieve_usages", lambda *a, **kw: None)

    called_conv = []
    called_spec = []
    monkeypatch.setattr(
        "superseded.context.gathering.discover_conventions",
        lambda root: (called_conv.append(True), "conv block")[1],
    )
    monkeypatch.setattr(
        "superseded.context.gathering.discover_repo_specs",
        lambda diff, root: (called_spec.append(True), "spec block")[1],
    )

    mock_engine = MagicMock()
    mock_engine.review.return_value = ReviewResult(findings=[])
    monkeypatch.setattr("superseded.cli.ReviewEngine.select", lambda *a, **kw: mock_engine)

    _run_review(
        pr=None,
        diff_range="HEAD~1..HEAD",
        agent=None,
        model=None,
        output_format="json",
        post=False,
        passes=None,
    )

    assert called_conv
    assert called_spec
    call_kwargs = mock_engine.review.call_args
    assert call_kwargs[1].get("conventions_signals") == "conv block"
    assert call_kwargs[1].get("spec_signals") == "spec block"


def test_no_conventions_flag_skips_discover(monkeypatch):
    monkeypatch.setattr(
        "superseded.cli.fetch_diff",
        lambda pr=None, diff_range=None, files=None, staged=False: "diff --git a/x.py b/x.py\n+x",
    )
    monkeypatch.setattr("superseded.cli.fetch_pr_description", lambda pr: None)
    monkeypatch.setattr(
        "superseded.context.gathering.compute_file_context", lambda d, root=None: None
    )
    monkeypatch.setattr("superseded.cli.current_repo", lambda: None)
    monkeypatch.setattr("superseded.context.gathering.run_static_analysis", lambda *a, **kw: None)
    monkeypatch.setattr("superseded.context.gathering.retrieve_usages", lambda *a, **kw: None)

    called = []
    monkeypatch.setattr(
        "superseded.context.gathering.discover_conventions",
        lambda root: (called.append("conv"), None)[1],
    )
    monkeypatch.setattr(
        "superseded.context.gathering.discover_repo_specs",
        lambda diff, root: (called.append("spec"), None)[1],
    )

    mock_engine = MagicMock()
    mock_engine.review.return_value = ReviewResult(findings=[])
    monkeypatch.setattr("superseded.cli.ReviewEngine.select", lambda *a, **kw: mock_engine)

    _run_review(
        pr=None,
        diff_range="HEAD~1..HEAD",
        agent=None,
        model=None,
        output_format="json",
        post=False,
        passes=None,
        no_conventions=True,
        no_specs=True,
    )

    assert not called
    call_kwargs = mock_engine.review.call_args
    assert call_kwargs[1].get("conventions_signals") is None
    assert call_kwargs[1].get("spec_signals") is None


@patch("superseded.cli._resolve_pr_review_diff")
@patch("superseded.cli.MemoryStore")
@patch("superseded.cli.current_repo")
@patch("superseded.cli.ReviewEngine")
@patch("superseded.context.gathering.compute_file_context")
@patch("superseded.cli.fetch_pr_description")
def test_review_progressive_writes_watermark_after_success(
    mock_desc, mock_ctx, mock_engine_cls, mock_repo, mock_store_cls, mock_resolve
):
    mock_desc.return_value = None
    mock_ctx.return_value = "ctx"
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    mock_engine.review.return_value = ReviewResult(findings=[])
    mock_engine.agent.is_available.return_value = True
    mock_repo.return_value = "owner/repo"
    store = FakeStore()
    mock_store_cls.return_value = store
    mock_resolve.return_value = ("DIFF", "incremental", "headsha")

    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--pr", "5"])

    assert result.exit_code == 0, result.output
    mock_resolve.assert_called_once()
    assert ("owner/repo", 5, "headsha") in store.set_watermark_calls


@patch("superseded.cli._resolve_pr_review_diff")
@patch("superseded.cli.MemoryStore")
@patch("superseded.cli.current_repo")
@patch("superseded.cli.ReviewEngine")
@patch("superseded.context.gathering.compute_file_context")
@patch("superseded.cli.fetch_pr_description")
def test_review_noop_when_identical(
    mock_desc, mock_ctx, mock_engine_cls, mock_repo, mock_store_cls, mock_resolve
):
    mock_desc.return_value = None
    mock_ctx.return_value = "ctx"
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    mock_engine.agent.is_available.return_value = True
    mock_repo.return_value = "owner/repo"
    store = FakeStore()
    mock_store_cls.return_value = store
    mock_resolve.return_value = (None, "noop", "headsha")

    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--pr", "5", "--format", "json"])

    assert result.exit_code == 0, result.output
    mock_engine.review.assert_not_called()
    assert store.set_watermark_calls == []


@patch("superseded.cli._resolve_pr_review_diff")
@patch("superseded.cli.MemoryStore")
@patch("superseded.cli.current_repo")
@patch("superseded.cli.ReviewEngine")
@patch("superseded.context.gathering.compute_file_context")
@patch("superseded.cli.fetch_pr_description")
def test_review_engine_failure_does_not_advance_watermark(
    mock_desc, mock_ctx, mock_engine_cls, mock_repo, mock_store_cls, mock_resolve
):
    mock_desc.return_value = None
    mock_ctx.return_value = "ctx"
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    mock_engine.agent.is_available.return_value = True
    mock_engine.review.side_effect = RuntimeError("agent crashed")
    mock_repo.return_value = "owner/repo"
    store = FakeStore()
    mock_store_cls.return_value = store
    mock_resolve.return_value = ("DIFF", "incremental", "headsha")

    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--pr", "5"])

    assert result.exit_code == 1, result.output
    assert store.set_watermark_calls == []


@patch("superseded.cli.fetch_pr_head_sha")
@patch("superseded.cli.fetch_incremental_diff")
@patch("superseded.cli.fetch_diff")
@patch("superseded.cli.MemoryStore")
@patch("superseded.cli.current_repo")
@patch("superseded.cli.ReviewEngine")
@patch("superseded.context.gathering.compute_file_context")
@patch("superseded.cli.fetch_pr_description")
def test_review_full_flag_skips_resolve_and_advances(
    mock_desc,
    mock_ctx,
    mock_engine_cls,
    mock_repo,
    mock_store_cls,
    mock_fetch_diff,
    mock_fetch_inc,
    mock_fetch_head,
):
    mock_desc.return_value = None
    mock_ctx.return_value = "ctx"
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    mock_engine.review.return_value = ReviewResult(findings=[])
    mock_engine.agent.is_available.return_value = True
    mock_repo.return_value = "owner/repo"
    store = FakeStore()
    mock_store_cls.return_value = store
    mock_fetch_diff.return_value = "FULLDIFF"
    mock_fetch_head.return_value = "headsha"
    mock_fetch_inc.return_value = ("INCDIFF", "ahead")

    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--pr", "5", "--full"])

    assert result.exit_code == 0, result.output
    mock_fetch_inc.assert_not_called()
    assert ("owner/repo", 5, "headsha") in store.set_watermark_calls


@patch("superseded.cli.fetch_diff")
@patch("superseded.cli.fetch_pr_head_sha")
@patch("superseded.cli.MemoryStore")
@patch("superseded.cli.current_repo")
@patch("superseded.cli.ReviewEngine")
@patch("superseded.context.gathering.compute_file_context")
@patch("superseded.cli.fetch_pr_description")
def test_review_init_real_store_before_progressive_read(
    mock_desc, mock_ctx, mock_engine_cls, mock_repo, mock_store_cls, mock_head, mock_diff, tmp_path
):
    """Regression: the CLI must init the real MemoryStore so the watermark table
    exists before the progressive path reads it (fresh-DB new-user flow).

    Keeps ``_resolve_pr_review_diff`` UNmocked so the real
    ``store.get_watermark`` runs against a fresh DB inside it — without the
    ``store.init()`` call in ``_run_review`` this raises OperationalError.
    """
    from superseded.memory.store import MemoryStore

    mock_desc.return_value = None
    mock_ctx.return_value = "ctx"
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    mock_engine.review.return_value = ReviewResult(findings=[])
    mock_engine.agent.is_available.return_value = True
    mock_repo.return_value = "owner/repo"
    mock_head.return_value = "headsha"
    mock_diff.return_value = "DIFF"

    real_store = MemoryStore(db_path=tmp_path / "fresh.db")
    mock_store_cls.return_value = real_store

    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--pr", "5"])

    assert result.exit_code == 0, result.output
    # The real store was initialized and the watermark row was written without
    # raising OperationalError (the regression this test guards against).
    assert asyncio.run(real_store.get_watermark("owner/repo", 5)) == "headsha"


@patch("superseded.cli._resolve_pr_review_diff")
@patch("superseded.cli.MemoryStore")
@patch("superseded.cli.current_repo")
@patch("superseded.cli.ReviewEngine")
@patch("superseded.context.gathering.compute_file_context")
@patch("superseded.cli.fetch_pr_description")
@patch("superseded.cli.fetch_diff")
def test_learned_context_injected_when_enabled(
    mock_fetch, mock_desc, mock_ctx, mock_engine_cls, mock_repo, mock_store_cls, mock_resolve
):
    from superseded.config import Config

    mock_fetch.return_value = "diff"
    mock_resolve.return_value = ("diff", "full", None)
    mock_ctx.return_value = "ctx"
    mock_desc.return_value = "PR body"
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    mock_engine.review.return_value = ReviewResult(findings=[])
    mock_engine.agent.is_available.return_value = True
    mock_repo.return_value = "owner/repo"

    store = FakeStore()
    store._learned_rules = [
        {
            "rule_text": "Inferred rule",
            "confidence": 0.9,
            "evidence_count": 3,
            "repo": "owner/repo",
            "created_at": "2025-01-01T00:00:00",
        }
    ]
    mock_store_cls.return_value = store

    with patch("superseded.cli.load_config", return_value=Config(learned_review=True)):
        runner = CliRunner()
        result = runner.invoke(cli, ["review", "--pr", "1"])

    assert result.exit_code == 0, result.output
    kwargs = mock_engine.review.call_args.kwargs
    learned = kwargs.get("learned_context")
    assert learned is not None
    assert "Inferred rule" in learned


@patch("superseded.cli._resolve_pr_review_diff")
@patch("superseded.cli.MemoryStore")
@patch("superseded.cli.current_repo")
@patch("superseded.cli.ReviewEngine")
@patch("superseded.context.gathering.compute_file_context")
@patch("superseded.cli.fetch_pr_description")
@patch("superseded.cli.fetch_diff")
def test_learned_context_is_none_when_disabled(
    mock_fetch, mock_desc, mock_ctx, mock_engine_cls, mock_repo, mock_store_cls, mock_resolve
):
    from superseded.config import Config

    mock_fetch.return_value = "diff"
    mock_resolve.return_value = ("diff", "full", None)
    mock_ctx.return_value = "ctx"
    mock_desc.return_value = "PR body"
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    mock_engine.review.return_value = ReviewResult(findings=[])
    mock_engine.agent.is_available.return_value = True
    mock_repo.return_value = "owner/repo"
    mock_store_cls.return_value = FakeStore()

    with patch("superseded.cli.load_config", return_value=Config(learned_review=False)):
        runner = CliRunner()
        result = runner.invoke(cli, ["review", "--pr", "1"])

    assert result.exit_code == 0, result.output
    kwargs = mock_engine.review.call_args.kwargs
    assert kwargs.get("learned_context") is None


@patch("superseded.cli.fetch_diff")
@patch("superseded.cli.ReviewEngine")
@patch("superseded.context.gathering.compute_file_context")
@patch("superseded.cli.fetch_pr_description")
def test_learned_context_none_when_no_memory(mock_desc, mock_ctx, mock_engine_cls, mock_fetch):
    mock_fetch.return_value = "diff"
    mock_ctx.return_value = "ctx"
    mock_desc.return_value = "PR body"
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    mock_engine.review.return_value = ReviewResult(findings=[])
    mock_engine.agent.is_available.return_value = True

    with patch("superseded.cli.current_repo", return_value=None):
        runner = CliRunner()
        result = runner.invoke(cli, ["review", "--pr", "5", "--no-memory"])

    assert result.exit_code == 0, result.output
    kwargs = mock_engine.review.call_args.kwargs
    assert kwargs.get("learned_context") is None


def test_migrate_command_creates_schema(tmp_path):
    db = tmp_path / "migrate.db"
    env = {"SUPERSEDED_DATABASE_URL": f"sqlite:///{db}"}
    runner = CliRunner()
    result = runner.invoke(cli, ["migrate"], env=env)
    assert result.exit_code == 0, result.output
    assert "0003" in result.output

    async def _has_findings() -> bool:
        async with aiosqlite.connect(db) as c:
            cur = await c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='findings'"
            )
            return await cur.fetchone() is not None

    assert asyncio.run(_has_findings())
