from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import get_args

import click

from superseded.config import Config, load_config
from superseded.context.conventions import discover_conventions
from superseded.context.spec_retrieval import discover_repo_specs
from superseded.context.static_analysis import run_static_analysis
from superseded.context.usage_retrieval import retrieve_usages
from superseded.diff import (
    compute_file_context,
    fetch_diff,
    fetch_pr_description,
    parse_diff_files,
    repo_root,
)
from superseded.memory.feedback import check_pr_feedback
from superseded.memory.store import MemoryStore
from superseded.models import PassName, ReviewResult
from superseded.output.github_pr import current_repo, post_review_to_pr
from superseded.output.json_out import format_json
from superseded.output.markdown import format_markdown
from superseded.output.table import format_table
from superseded.review.engine import ReviewEngine

AGENT_ENV = "SUPERSEDED_AGENT"
MODEL_ENV = "SUPERSEDED_MODEL"
DEFAULT_TIMEOUT = 300
KNOWN_PASSES: list[str] = list(get_args(PassName))

try:
    _VERSION = version("superseded")
except PackageNotFoundError:  # pragma: no cover - editable/installed environments
    _VERSION = "0.0.0+unknown"

logger = logging.getLogger(__name__)


def _status(message: str) -> None:
    """Emit a human-readable status message.

    Routed to stderr so it never pollutes structured stdout output
    (e.g. ``--format json | jq``).
    """
    click.echo(message, err=True)


def resolve_agent(agent_flag: str | None, config: Config) -> str:
    return os.environ.get(AGENT_ENV) or agent_flag or config.agent


def resolve_model(model_flag: str | None, config: Config) -> str | None:
    return os.environ.get(MODEL_ENV) or model_flag or config.model


def _parse_passes(raw: str | None) -> list[str] | None:
    """Parse and validate a comma-separated --passes string.

    Returns ``None`` when no flag was supplied (caller should fall back to
    config-enabled passes). Exits with a clear error on unknown pass names so
    typos don't silently run zero passes.
    """
    if raw is None:
        return None
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        return []
    unknown = [p for p in parts if p not in KNOWN_PASSES]
    if unknown:
        click.echo(
            f"Error: unknown pass name(s): {', '.join(unknown)}. "
            f"Choose from: {', '.join(KNOWN_PASSES)}",
            err=True,
        )
        sys.exit(2)
    return parts


def _detect_current_pr() -> int | None:
    """Auto-detect the PR for the current branch via ``gh``.

    Returns ``None`` if gh is unavailable, not authenticated, or the branch has
    no open PR. Never raises — it's a best-effort convenience for
    ``feedback --check`` without ``--pr``.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "view", "--json", "number", "-q", ".number"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired:
        return None
    body = result.stdout.strip()
    return int(body) if body.isdigit() else None


def _progress(pass_name: str, status: str) -> None:
    """Per-pass progress callback streamed to stderr."""
    _status(f"[{pass_name}] {status}")


def format_memory_context(dismissed: list[dict]) -> str | None:
    if not dismissed:
        return None
    lines = []
    for f in dismissed:
        pass_name = f.get("pass") or f.get("pass_name") or "review"
        title = f.get("title", "")
        reasoning = f.get("reasoning", "")
        line = f'- {pass_name.title()} pass: "{title}" — dismissed by human review.'
        if reasoning:
            truncated = reasoning[:300]
            if len(reasoning) > 300:
                truncated += f"\u2026 ({len(reasoning)} chars)"
            line += f'\n  Rationale then was: "{truncated}"'
        lines.append(line)
    return "\n".join(lines)


@click.group()
@click.version_option(version=_VERSION)
def cli() -> None:
    """Superseded — multi-pass AI code review tool."""


@cli.command()
@click.option("--pr", type=int, help="PR number to review")
@click.option("--diff", "diff_range", help="Git diff range (e.g. HEAD~3..HEAD)")
@click.option("--agent", default=None, help="AI CLI agent (claude-code, opencode, codex)")
@click.option("--model", default=None, help="Model to use")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["json", "markdown", "table"]),
    default=None,
    help="Output format",
)
@click.option("--post", is_flag=True, help="Post review to GitHub PR (requires --pr)")
@click.option(
    "--passes",
    default=None,
    help=f"Comma-separated passes to run (one of: {', '.join(KNOWN_PASSES)})",
)
@click.option(
    "--timeout",
    type=int,
    default=None,
    help=f"Per-pass agent timeout in seconds (default: {DEFAULT_TIMEOUT})",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to config file (default: .superseded.yaml in repo root)",
)
@click.option("--no-memory", is_flag=True, help="Disable memory feedback injection")
@click.option("--no-static", is_flag=True, help="Disable static analysis")
@click.option("--no-usage", is_flag=True, help="Disable usage retrieval")
@click.option("--no-conventions", is_flag=True, help="Disable project conventions injection")
@click.option("--no-specs", is_flag=True, help="Disable design spec/plan retrieval")
@click.argument("files", nargs=-1, type=click.Path(exists=True, dir_okay=False))
def review(
    pr: int | None,
    diff_range: str | None,
    agent: str | None,
    model: str | None,
    output_format: str | None,
    post: bool,
    passes: str | None,
    timeout: int | None,
    config_path: Path | None,
    no_memory: bool,
    no_static: bool,
    no_usage: bool,
    no_conventions: bool,
    no_specs: bool,
    files: tuple[str, ...],
) -> None:
    """Review code changes.

    Review a PR (`--pr 123`), a local diff (`--diff HEAD~3..HEAD`), or specific
    files (`superseded review src/auth.py`). File paths are combined with
    `--diff` (defaulting to `HEAD` if only files are given) and cannot be used
    with `--pr`.
    """
    if pr is None and diff_range is None and not files:
        click.echo(
            "Error: Provide either --pr, --diff, or one or more FILES to review.",
            err=True,
        )
        sys.exit(2)

    if post and pr is None:
        click.echo("Error: --post requires --pr (cannot post from a local diff).", err=True)
        sys.exit(2)

    if pr is not None and files:
        click.echo("Error: positional FILES cannot be combined with --pr.", err=True)
        sys.exit(2)

    pass_list = _parse_passes(passes)

    _run_review(
        pr=pr,
        diff_range=diff_range,
        agent=agent,
        model=model,
        output_format=output_format,
        post=post,
        passes=pass_list,
        timeout=timeout,
        config_path=config_path,
        no_memory=no_memory,
        no_static=no_static,
        no_usage=no_usage,
        no_conventions=no_conventions,
        no_specs=no_specs,
        files=list(files) or None,
    )


def _run_review(
    pr: int | None,
    diff_range: str | None,
    agent: str | None,
    model: str | None,
    output_format: str | None,
    post: bool,
    passes: list[str] | None,
    *,
    timeout: int | None = None,
    config_path: Path | None = None,
    no_memory: bool = False,
    no_static: bool = False,
    no_usage: bool = False,
    no_conventions: bool = False,
    no_specs: bool = False,
    files: list[str] | None = None,
) -> None:
    config = load_config(config_path)
    agent_name = resolve_agent(agent, config)
    model_name = resolve_model(model, config)
    fmt = output_format or config.format

    # Select the agent and verify it is installed BEFORE doing expensive context
    # work. Failing fast here saves wasted diff fetch / static analysis when the
    # user simply forgot to install the CLI.
    try:
        engine = ReviewEngine.select(agent_name, model=model_name, config=config)
    except ValueError as err:
        click.echo(f"Error: {err}", err=True)
        sys.exit(2)
    if not engine.agent.is_available():
        click.echo(
            f"Error: Agent CLI '{engine.agent.name}' not found on PATH. "
            "Install it or choose a different agent with --agent.",
            err=True,
        )
        sys.exit(2)

    _status("Fetching diff...")
    diff = fetch_diff(pr=pr, diff_range=diff_range, files=files)

    _status("Gathering context...")
    root = repo_root()
    file_context = compute_file_context(diff, root=root) or None
    pr_description = fetch_pr_description(pr) if pr is not None else None

    enable_static = config.static_analysis and not no_static
    enable_usage = config.usage_retrieval and not no_usage
    static_signals: str | None = None
    usage_signals: str | None = None
    if enable_static:
        changed_files = [e["file"] for e in parse_diff_files(diff)]
        static_signals = run_static_analysis(changed_files, root)
    if enable_usage:
        usage_signals = retrieve_usages(diff, root)

    enable_conventions = config.conventions and not no_conventions
    enable_specs = config.spec_retrieval and not no_specs
    conventions_signals: str | None = None
    spec_signals: str | None = None
    if enable_conventions:
        conventions_signals = discover_conventions(root)
    if enable_specs:
        spec_signals = discover_repo_specs(diff, root)

    repo = current_repo()
    memory_context: str | None = None
    store: MemoryStore | None = None
    if config.memory and not no_memory and repo:
        store = MemoryStore()
        dismissed = asyncio.run(_load_dismissed(store, repo))
        memory_context = format_memory_context(dismissed)

    pass_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
    _status(f"Running review with {agent_name} (timeout {pass_timeout}s per pass)...")
    try:
        result = engine.review(
            diff=diff,
            pr_description=pr_description,
            file_context=file_context,
            memory_context=memory_context,
            static_signals=static_signals,
            usage_signals=usage_signals,
            conventions_signals=conventions_signals,
            spec_signals=spec_signals,
            passes=passes,
            timeout=pass_timeout,
            progress=_progress,
        )
    except RuntimeError as err:
        click.echo(f"Error: {err}", err=True)
        sys.exit(1)

    if fmt == "json":
        click.echo(format_json(result))
    elif fmt == "markdown":
        click.echo(format_markdown(result))
    else:
        click.echo(format_table(result))

    for w in result.warnings:
        click.echo(f"\nWarning: {w}", err=True)

    if store is not None and repo:
        asyncio.run(_persist_findings(store, result, repo))

    if post and pr is not None:
        _status("Posting to GitHub PR...")
        comment_ids = post_review_to_pr(pr=pr, result=result)
        if store is not None:
            asyncio.run(_link_comment_ids(store, result, comment_ids))
        _status(f"Done. Posted {len(comment_ids)} comment(s).")


async def _load_dismissed(store: MemoryStore, repo: str) -> list[dict]:
    await store.init()
    return await store.get_dismissed_findings(repo)


async def _persist_findings(store: MemoryStore, result: ReviewResult, repo: str) -> None:
    for f in result.findings:
        await store.record_finding(
            finding_id=f.id,
            repo=repo,
            pass_name=f.pass_name,
            severity=f.severity,
            file=f.file,
            line=f.line,
            title=f.title,
            description=f.description,
            reasoning=f.reasoning,
        )


async def _link_comment_ids(
    store: MemoryStore, result: ReviewResult, comment_ids: list[int | None]
) -> None:
    for finding, comment_id in zip(result.findings, comment_ids, strict=True):
        if comment_id is not None:
            await store.set_comment_id(finding.id, comment_id)


@cli.command()
@click.option("--check", is_flag=True, help="Check for feedback on past reviews")
@click.option(
    "--pr",
    type=int,
    default=None,
    help="PR number to check for feedback (auto-detected from current branch if omitted)",
)
@click.argument("comment_id", required=False)
@click.option("--helpful", is_flag=True)
@click.option("--dismiss", is_flag=True)
@click.pass_context
def feedback(
    ctx: click.Context,
    check: bool,
    pr: int | None,
    comment_id: str | None,
    helpful: bool,
    dismiss: bool,
) -> None:
    """Manage review feedback."""
    if check:
        pr_number = pr if pr is not None else _detect_current_pr()
        if pr_number is None:
            click.echo(
                "Error: --check requires --pr <number>, or run inside a branch with an "
                "open PR (auto-detection via 'gh pr view' failed).",
                err=True,
            )
            sys.exit(2)
        _run_feedback_check(pr_number)
        return

    if helpful and dismiss:
        click.echo("Error: --helpful and --dismiss are mutually exclusive.", err=True)
        sys.exit(2)

    if comment_id is not None and (helpful ^ dismiss):
        action = "helpful" if helpful else "dismiss"
        _run_feedback_manual(comment_id, action)
        return

    click.echo(ctx.get_help())
    ctx.exit(2)


def _run_feedback_check(pr: int) -> None:
    repo = current_repo()
    if repo is None:
        click.echo("Error: could not resolve current repository (is gh authenticated?).", err=True)
        sys.exit(1)
    comments = check_pr_feedback(pr=pr, repo=repo)
    if not comments:
        click.echo("No past review comments found on this PR.")
        return
    store = MemoryStore()
    asyncio.run(_process_feedback(store, comments))


async def _process_feedback(store: MemoryStore, comments: list[dict]) -> None:
    await store.init()
    recorded = 0
    for c in comments:
        cid = c.get("id")
        if cid is None:
            continue
        action = _classify_feedback(c)
        if action is None:
            continue
        ok = await store.record_feedback_by_comment_id(int(cid), action)
        if ok:
            recorded += 1
    if recorded:
        click.echo(f"Recorded feedback for {recorded} comment(s).")
    else:
        click.echo("No actionable feedback found.")


@cli.command()
@click.option("--port", type=int, default=None, help="Server port")
@click.option("--host", default=None, help="Server host")
@click.option("--config", "config_path", default=None, help="Server config file path")
def serve(port: int | None, host: str | None, config_path: str | None) -> None:
    """Start the Superseded review server."""
    from contextlib import asynccontextmanager

    from superseded.server.config import ServerConfig
    from superseded.server.github import GitHubApp
    from superseded.server.repo_manager import RepoManager
    from superseded.server.worker import ReviewWorker

    config = ServerConfig.from_yaml(Path(config_path)) if config_path else ServerConfig.from_env()

    if port is not None:
        config.port = port
    if host is not None:
        config.host = host

    github = GitHubApp(
        app_id=config.app_id,
        private_key_path=config.private_key_path,
        webhook_secret=config.webhook_secret,
    )
    repo_manager = RepoManager(base_path=config.temp_dir)
    worker = ReviewWorker(
        github=github,
        repo_manager=repo_manager,
        max_concurrent=config.max_concurrent_reviews,
    )

    import uvicorn

    from superseded.server.app import create_app
    from superseded.server.lifecycle import JsonFormatter, ServerLifecycle

    lifecycle = ServerLifecycle(worker=worker)

    @asynccontextmanager
    async def lifespan(_app):
        await lifecycle.startup()
        try:
            yield
        finally:
            await lifecycle.shutdown()

    app = create_app(
        config=config,
        github=github,
        worker=worker,
        repo_manager=repo_manager,
        store=MemoryStore(),
        lifespan=lifespan,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        handlers=[handler],
    )

    _status(f"Starting Superseded server on {config.host}:{config.port}")
    uvicorn.run(app, host=config.host, port=config.port, log_level=config.log_level)


async def _apply_feedback(store: MemoryStore, comment_id: int, action: str) -> bool:
    await store.init()
    return await store.record_feedback_by_comment_id(comment_id, action)


def _classify_feedback(comment: dict) -> str | None:
    if comment.get("resolved"):
        return "dismiss"
    reactions = comment.get("reactions") or {}
    if reactions.get("-1", 0) > 0:
        return "dismiss"
    if reactions.get("+1", 0) > 0:
        return "helpful"
    return None


def _run_feedback_manual(comment_id: str, action: str) -> None:
    try:
        cid = int(comment_id)
    except ValueError:
        click.echo(f"Error: comment id must be numeric, got {comment_id!r}.", err=True)
        sys.exit(1)
    store = MemoryStore()
    ok = asyncio.run(_apply_feedback(store, cid, action))
    if not ok:
        click.echo(
            f"Error: no stored finding for GitHub comment id {cid}. "
            "Run 'superseded review --post' first so the comment id is mapped.",
            err=True,
        )
        sys.exit(1)
    click.echo(f"Recorded {action} for comment {cid}.")
