from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import signal
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, get_args

import click

from superseded.audit.guidelines import assemble_learned_context, format_memory_context
from superseded.audit.reflector import PatternReflector
from superseded.audit.stats import StatsAggregator
from superseded.config import Config, load_config, write_config
from superseded.context.gathering import gather_context
from superseded.diff import (
    fetch_diff,
    fetch_pr_description,
    fetch_pr_head_sha,
    repo_root,
)
from superseded.incremental import IncrementalDiffError, fetch_incremental_diff
from superseded.logging_utils import setup_logging
from superseded.memory.feedback import check_pr_feedback
from superseded.memory.store import MemoryStore
from superseded.models import PassName, ReviewResult
from superseded.output.github_pr import current_repo, post_review_to_pr
from superseded.output.json_out import format_json
from superseded.output.markdown import format_markdown
from superseded.output.table import format_table
from superseded.providers import (
    AnthropicProvider,
    DeepSeekProvider,
    OpenAIProvider,
    Provider,
    ProviderConfigError,
)
from superseded.review.engine import ReviewEngine
from superseded.server.client import ServerReviewError, review_via_server

if TYPE_CHECKING:
    from superseded.server.config import ServerConfig

MODEL_ENV = "SUPERSEDED_MODEL"
REASONING_EFFORT_ENV = "SUPERSEDED_REASONING_EFFORT"
GRAPH_ENV = "SUPERSEDED_GRAPH"
VERIFY_ENV = "SUPERSEDED_VERIFY"
LOG_FORMAT_ENV = "SUPERSEDED_LOG_FORMAT"
LOG_LEVEL_ENV = "SUPERSEDED_LOG_LEVEL"
VERBOSE_ENV = "VERBOSE"
SERVER_URL_ENV = "SUPERSEDED_SERVER_URL"
SERVER_KEY_ENV = "SUPERSEDED_SERVER_KEY"
_TRUTHY = ("1", "true", "yes", "on")


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


DEFAULT_TIMEOUT = 600
# Distinct exit code for a partial review: the run completed and produced
# output, but at least one pass was skipped (e.g. transient provider failure).
# Lets CI/scripts distinguish clean (0) from infra degradation (3) from a hard
# error (1) / usage error (2). Only the local CLI path uses process exit codes;
# the server path surfaces failures via the check-run conclusion instead.
EXIT_PARTIAL_FAILURE = 3
KNOWN_PASSES: list[str] = list(get_args(PassName))
EFFORT_LEVELS: list[str] = ["low", "medium", "high", "max"]

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


PROVIDER_ENV = "SUPERSEDED_PROVIDER"


def resolve_provider(provider_flag: str | None, config: Config) -> str:
    legacy = os.environ.get("SUPERSEDED_AGENT")
    env_provider = os.environ.get(PROVIDER_ENV)
    if legacy and not env_provider:
        import warnings

        warnings.warn(
            "SUPERSEDED_AGENT is deprecated; use SUPERSEDED_PROVIDER.",
            DeprecationWarning,
            stacklevel=2,
        )
        return legacy
    if env_provider:
        return env_provider
    if provider_flag is not None:
        return provider_flag
    return config.provider


def resolve_model(model_flag: str | None, config: Config) -> str | None:
    return os.environ.get(MODEL_ENV) or model_flag or config.model


def resolve_reasoning_effort(flag: str | None, config: Config) -> str:
    env = os.environ.get(REASONING_EFFORT_ENV)
    value = env or flag or config.reasoning_effort
    if value not in EFFORT_LEVELS:
        click.echo(
            f"Error: invalid reasoning effort {value!r}. Choose from: {', '.join(EFFORT_LEVELS)}.",
            err=True,
        )
        sys.exit(2)
    return value


def _build_server_provider(config: ServerConfig) -> Provider:
    if config.provider == "openai":
        return OpenAIProvider(api_key=config.openai_api_key)
    if config.provider == "anthropic":
        return AnthropicProvider(api_key=config.anthropic_api_key)
    return DeepSeekProvider(api_key=config.deepseek_api_key)


def resolve_graph(cli_value: bool | None, config: Config) -> bool:
    env = os.environ.get(GRAPH_ENV)
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    if cli_value is not None:
        return cli_value
    return config.graph


def resolve_verify(cli_value: bool | None, config: Config) -> bool:
    env = os.environ.get(VERIFY_ENV)
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    if cli_value is not None:
        return cli_value
    return config.verify


def resolve_server(server_flag: str | None, config: Config) -> str | None:
    return os.environ.get(SERVER_URL_ENV) or server_flag or config.server


def resolve_server_key(key_flag: str | None, config: Config) -> str | None:
    return os.environ.get(SERVER_KEY_ENV) or key_flag or config.server_key


def resolve_log_format(flag: str | None, config: Config | None = None) -> str:
    return os.environ.get(LOG_FORMAT_ENV) or flag or (config.log_format if config else "text")


def resolve_log_level(flag: str | None, config: Config | None = None) -> str:
    if _env_truthy(VERBOSE_ENV):
        return "DEBUG"
    return os.environ.get(LOG_LEVEL_ENV) or flag or (config.log_level if config else "WARNING")


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


def _resolve_pr_review_diff(
    pr: int,
    repo: str,
    store: MemoryStore,
    full: bool,
) -> tuple[str | None, str, str]:
    """Resolve the diff for a ``--pr`` review, applying progressive logic.

    Returns ``(diff, mode, head_sha)``:
      mode "noop"        -> diff is None; no new commits; caller emits empty result
      mode "full"        -> full PR diff (first review, --full, or no watermark)
      mode "incremental" -> diff since the watermark
      mode "fallback"    -> full diff after a stale watermark or compare-API error

    The caller writes ``store.set_watermark(repo, pr, head_sha)`` after a
    successful review for every mode except "noop".
    """
    head_sha = fetch_pr_head_sha(pr)
    watermark = asyncio.run(store.get_watermark(repo, pr))

    if watermark is None or full:
        return fetch_diff(pr=pr), "full", head_sha

    owner, _, name = repo.partition("/")
    try:
        patch, status = fetch_incremental_diff(owner, name, watermark, head_sha)
    except IncrementalDiffError:
        _status(f"watermark {watermark[:7]} unreachable; falling back to full review")
        return fetch_diff(pr=pr), "fallback", head_sha

    if status == "identical":
        return None, "noop", head_sha
    if status == "ahead":
        _status(f"Reviewing new commits since {watermark[:7]}...")
        return patch, "incremental", head_sha

    _status(f"watermark {watermark[:7]} no longer an ancestor; falling back to full review")
    return fetch_diff(pr=pr), "fallback", head_sha


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


@click.group()
@click.version_option(version=_VERSION)
@click.option(
    "--log-format",
    "log_format",
    type=click.Choice(["text", "json"]),
    default=None,
    help="Log output format (default: text). Env: SUPERSEDED_LOG_FORMAT.",
)
@click.option(
    "--log-level",
    "log_level",
    default=None,
    help="Log level (e.g. DEBUG/INFO/WARNING). Env: SUPERSEDED_LOG_LEVEL. "
    "VERBOSE=1 forces DEBUG (overrides everything).",
)
@click.pass_context
def cli(ctx: click.Context, log_format: str | None, log_level: str | None) -> None:
    """Superseded — reviews that supersede themselves."""
    ctx.obj = {"log_format": log_format, "log_level": log_level}


@cli.command()
@click.option("--pr", type=int, help="PR number to review")
@click.option("--diff", "diff_range", help="Git diff range (e.g. HEAD~3..HEAD)")
@click.option("--provider", default=None, help="Model provider (deepseek, openai, anthropic)")
@click.option("--model", default=None, help="Model to use")
@click.option(
    "--reasoning-effort",
    "reasoning_effort",
    type=click.Choice(EFFORT_LEVELS),
    default=None,
    help="Reasoning depth (low|medium|high|max; mapped per provider). Env: SUPERSEDED_REASONING_EFFORT.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["json", "markdown", "table"]),
    default=None,
    help="Output format",
)
@click.option("--post", is_flag=True, help="Post review to GitHub PR (requires --pr)")
@click.option("--server", "server_url_flag", default=None, help="Review server URL (server-mode).")
@click.option("--server-key", "server_key_flag", default=None, help="Review server bearer key.")
@click.option("--owner", default=None, help="PR repo owner (defaults to current git remote).")
@click.option(
    "--repo", "repo_name", default=None, help="PR repo name (defaults to current git remote)."
)
@click.option(
    "--no-post", "no_post", is_flag=True, help="Suppress server-side PR posting (server-mode)."
)
@click.option(
    "--passes",
    default=None,
    help=f"Comma-separated passes to run (one of: {', '.join(KNOWN_PASSES)})",
)
@click.option(
    "--timeout",
    type=int,
    default=None,
    help=f"Per-pass provider timeout in seconds (default: {DEFAULT_TIMEOUT})",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to config file (default: .superseded.yaml in repo root)",
)
@click.option("--no-memory", is_flag=True, help="Disable memory feedback injection")
@click.option(
    "--full",
    "full_review",
    is_flag=True,
    help="Force a full review (ignore progressive watermark)",
)
@click.option("--no-static", is_flag=True, help="Disable static analysis")
@click.option("--no-usage", is_flag=True, help="Disable usage retrieval")
@click.option("--no-conventions", is_flag=True, help="Disable project conventions injection")
@click.option("--no-specs", is_flag=True, help="Disable design spec/plan retrieval")
@click.option(
    "--staged",
    is_flag=True,
    help="Review staged (cached) changes only; default reviews all uncommitted changes.",
)
@click.option(
    "--graph/--no-graph",
    "graph",
    default=None,
    help="Toggle graph-grounded usage retrieval (default: from config)",
)
@click.option(
    "--verify/--no-verify",
    "verify",
    default=None,
    help="Toggle post-merge verification pass (default: from config; env SUPERSEDED_VERIFY).",
)
@click.argument("files", nargs=-1, type=click.Path(exists=True, dir_okay=False))
@click.pass_context
def review(
    ctx: click.Context,
    pr: int | None,
    diff_range: str | None,
    provider: str | None,
    model: str | None,
    reasoning_effort: str | None,
    output_format: str | None,
    post: bool,
    server_url_flag: str | None,
    server_key_flag: str | None,
    owner: str | None,
    repo_name: str | None,
    no_post: bool,
    passes: str | None,
    timeout: int | None,
    config_path: Path | None,
    no_memory: bool,
    full_review: bool,
    no_static: bool,
    no_usage: bool,
    no_conventions: bool,
    no_specs: bool,
    graph: bool | None,
    verify: bool | None,
    staged: bool,
    files: tuple[str, ...],
) -> None:
    """Review code changes.

    Review a PR (`--pr 123`), a local diff (`--diff HEAD~3..HEAD`), or specific
    files (`superseded review src/auth.py`). File paths are combined with
    `--diff` (defaulting to `HEAD` if only files are given) and cannot be used
    with `--pr`.
    """
    log_config = load_config(config_path)
    setup_logging(
        resolve_log_format(ctx.obj.get("log_format") if ctx.obj else None, log_config),
        resolve_log_level(ctx.obj.get("log_level") if ctx.obj else None, log_config),
    )

    server_url = resolve_server(server_url_flag, log_config)
    if server_url:
        if files or diff_range or staged:
            click.echo(
                "Error: --server cannot be combined with --diff/--files/--staged.",
                err=True,
            )
            sys.exit(2)
        if log_config.server and not os.environ.get(SERVER_URL_ENV) and server_url_flag is None:
            _status(
                f"Warning: server-mode enabled by 'server:' in "
                f"{config_path or '.superseded.yaml'}. Use SUPERSEDED_SERVER_URL or "
                "--server to override; remove the key to disable."
            )
        _run_review_remote(
            server_url=server_url,
            server_key=resolve_server_key(server_key_flag, log_config),
            pr=pr,
            owner_flag=owner,
            repo_flag=repo_name,
            post=not no_post,
            post_flag_set=post,
            ignored_flags=_ignored_server_mode_flags(
                provider=provider,
                model=model,
                reasoning_effort=reasoning_effort,
                no_memory=no_memory,
                full_review=full_review,
                no_static=no_static,
                no_usage=no_usage,
                no_conventions=no_conventions,
                no_specs=no_specs,
                graph=graph,
                verify=verify,
            ),
            output_format=output_format,
            passes=passes,
            timeout=timeout,
            config_path=config_path,
        )
        return

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
        provider=provider,
        model=model,
        reasoning_effort=reasoning_effort,
        output_format=output_format,
        post=post,
        passes=pass_list,
        timeout=timeout,
        config_path=config_path,
        no_memory=no_memory,
        full=full_review,
        no_static=no_static,
        no_usage=no_usage,
        no_conventions=no_conventions,
        no_specs=no_specs,
        graph=graph,
        verify=verify,
        staged=staged,
        files=list(files) or None,
    )


def _run_review(
    pr: int | None,
    diff_range: str | None,
    provider: str | None,
    model: str | None,
    output_format: str | None,
    post: bool,
    passes: list[str] | None,
    *,
    reasoning_effort: str | None = None,
    timeout: int | None = None,
    config_path: Path | None = None,
    no_memory: bool = False,
    full: bool = False,
    no_static: bool = False,
    no_usage: bool = False,
    no_conventions: bool = False,
    no_specs: bool = False,
    graph: bool | None = None,
    verify: bool | None = None,
    staged: bool = False,
    files: list[str] | None = None,
) -> None:
    config = load_config(config_path)
    verify = resolve_verify(verify, config)
    config.verify = verify
    provider_name = resolve_provider(provider, config)
    model_name = resolve_model(model, config)
    reasoning_effort = resolve_reasoning_effort(reasoning_effort, config)
    config.reasoning_effort = reasoning_effort
    fmt = output_format or config.format
    post = post or config.post_to_pr

    try:
        engine = ReviewEngine.select(provider_name, model=model_name, config=config)
    except (ValueError, ProviderConfigError) as err:
        click.echo(f"Error: {err}", err=True)
        sys.exit(2)

    pass_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT

    repo = current_repo()
    memory_enabled = config.memory and not no_memory and repo is not None
    progressive_active = memory_enabled and config.progressive and pr is not None

    store: MemoryStore | None = None
    if memory_enabled:
        store = MemoryStore()
        asyncio.run(store.init())

    _prev_sigint = signal.getsignal(signal.SIGINT)

    def _sigint_handler(signum: int, frame: object) -> None:
        if store is not None:
            with contextlib.suppress(Exception):
                asyncio.run(store.close())
        if callable(_prev_sigint):
            _prev_sigint(signum, frame)
        else:
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _sigint_handler)

    try:
        head_sha: str | None = None

        if progressive_active:
            assert repo is not None
            assert store is not None
            try:
                diff, mode, head_sha = _resolve_pr_review_diff(
                    pr=pr, repo=repo, store=store, full=full
                )
            except RuntimeError as err:
                click.echo(f"Error: {err}", err=True)
                sys.exit(1)
            if mode == "noop":
                _status("No new commits since last review.")
                empty = ReviewResult(findings=[], warnings=[])
                if fmt == "json":
                    click.echo(format_json(empty))
                elif fmt == "markdown":
                    click.echo(format_markdown(empty))
                else:
                    click.echo(format_table(empty))
                return
            _status("Gathering context...")
        else:
            if pr is not None and not memory_enabled:
                _status("memory disabled; running full review (progressive review needs memory)")
            _status("Fetching diff...")
            try:
                diff = fetch_diff(pr=pr, diff_range=diff_range, files=files, staged=staged)
            except ValueError as err:
                click.echo(f"Error: {err}", err=True)
                sys.exit(2)
            except RuntimeError as err:
                click.echo(f"Error: {err}", err=True)
                sys.exit(1)
            _status("Gathering context...")

        root = repo_root()

        enable_static = config.static_analysis and not no_static
        enable_usage = config.usage_retrieval and not no_usage
        enable_conventions = config.conventions and not no_conventions
        enable_specs = config.spec_retrieval and not no_specs
        enable_graph = resolve_graph(graph, config)

        pr_description = fetch_pr_description(pr) if pr is not None else None

        context = gather_context(
            diff,
            root,
            static_analysis=enable_static,
            usage_retrieval=enable_usage,
            conventions=enable_conventions,
            spec_retrieval=enable_specs,
            graph=enable_graph,
        )
        file_context = context["file_context"]
        static_signals = context["static_signals"]
        usage_signals = context["usage_signals"]
        conventions_signals = context["conventions_signals"]
        spec_signals = context["spec_signals"]

        memory_context: str | None = None
        if store is not None and repo:
            dismissed = asyncio.run(_load_dismissed(store, repo))
            memory_context = format_memory_context(dismissed)

        learned_context: str | None = None
        if config.learned_review and store is not None and repo:
            learned_context = asyncio.run(_build_learned_context(store, engine, repo, config, root))

        _status(f"Running review with {provider_name} (timeout {pass_timeout}s per pass)...")
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
                learned_context=learned_context,
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
            asyncio.run(_post_review_store(store, result, repo, head_sha, pr, post, diff))
        elif post and pr is not None:
            _status("Posting to GitHub PR...")
            comment_ids = post_review_to_pr(pr=pr, result=result, diff=diff)
            _status(f"Done. Posted {len(comment_ids)} comment(s).")

        # Surface partial failures via the process exit code so CI can detect
        # that one or more passes were skipped (e.g. provider hiccups). Emitted
        # only after output/persistence complete so it never short-circuits
        # posting or storing. The `finally` block still runs before the exit.
        if result.warnings:
            sys.exit(EXIT_PARTIAL_FAILURE)
    finally:
        signal.signal(signal.SIGINT, _prev_sigint)
        if store is not None:
            with contextlib.suppress(Exception):
                asyncio.run(store.close())


async def _post_review_store(
    store: MemoryStore,
    result: ReviewResult,
    repo: str,
    head_sha: str | None,
    pr: int | None,
    post: bool,
    diff: str,
) -> None:
    """Persist findings, watermark, and comment links in one event loop."""
    async with store:
        if result.findings:
            await store.record_findings_batch(
                [
                    {
                        "id": f.id,
                        "pass_name": f.pass_name,
                        "severity": f.severity,
                        "file": f.file,
                        "line": f.line,
                        "title": f.title,
                        "description": f.description,
                        "reasoning": f.reasoning,
                        "verification": f.verification,
                        "verification_reason": f.verification_reason,
                    }
                    for f in result.findings
                ],
                repo,
            )

            if result.dropped_findings:
                for f in result.dropped_findings:
                    await store.record_verification_feedback(f.id)

        if head_sha is not None and pr is not None:
            await store.set_watermark(repo, pr, head_sha)

        if post and pr is not None:
            _status("Posting to GitHub PR...")
            comment_ids = post_review_to_pr(pr=pr, result=result, diff=diff)
            pairs = [
                (f.id, cid)
                for f, cid in zip(result.findings, comment_ids, strict=True)
                if cid is not None
            ]
            if pairs:
                await store.set_comment_ids_batch(pairs)
            _status(f"Done. Posted {len(comment_ids)} comment(s).")


async def _load_dismissed(store: MemoryStore, repo: str) -> list[dict]:
    async with store:
        return await store.get_dismissed_findings(repo)


async def _build_learned_context(
    store: MemoryStore,
    engine: ReviewEngine,
    repo: str,
    config: Config,
    root: Path,
) -> str | None:
    aggregator = StatsAggregator(store)
    await aggregator._refresh(repo)
    stats_text = await aggregator.get_stats_context(repo)

    reflector = PatternReflector(
        provider=engine.provider, store=store, threshold=config.reflection_threshold
    )
    await reflector.maybe_reflect(repo)

    await store.prune_stale_rules(repo)
    all_rules = await store.get_learned_rules(repo, limit=config.max_learned_rules)
    return assemble_learned_context(stats_text, all_rules, config.max_learned_rules)


def _ignored_server_mode_flags(
    *,
    provider: str | None,
    model: str | None,
    reasoning_effort: str | None,
    no_memory: bool,
    full_review: bool,
    no_static: bool,
    no_usage: bool,
    no_conventions: bool,
    no_specs: bool,
    graph: bool | None,
    verify: bool | None,
) -> list[str]:
    """Flag names the user passed that server-mode silently ignores."""
    flags: list[str] = []
    if provider is not None:
        flags.append("--provider")
    if model is not None:
        flags.append("--model")
    if reasoning_effort is not None:
        flags.append("--reasoning-effort")
    if no_memory:
        flags.append("--no-memory")
    if full_review:
        flags.append("--full")
    if no_static:
        flags.append("--no-static")
    if no_usage:
        flags.append("--no-usage")
    if no_conventions:
        flags.append("--no-conventions")
    if no_specs:
        flags.append("--no-specs")
    if graph is not None:
        flags.append("--graph" if graph else "--no-graph")
    if verify is not None:
        flags.append("--verify" if verify else "--no-verify")
    return flags


def _run_review_remote(
    *,
    server_url: str,
    server_key: str | None,
    pr: int | None,
    owner_flag: str | None,
    repo_flag: str | None,
    post: bool,
    post_flag_set: bool,
    ignored_flags: list[str],
    output_format: str | None,
    passes: str | None,
    timeout: int | None,
    config_path: Path | None,
) -> None:
    if pr is None:
        click.echo("Error: --server requires --pr.", err=True)
        sys.exit(2)
    if server_key is None:
        click.echo(
            "Error: server key required. Set --server-key, SUPERSEDED_SERVER_KEY, "
            "or 'server_key:' in .superseded.yaml.",
            err=True,
        )
        sys.exit(2)

    owner = owner_flag
    repo = repo_flag
    if owner is None or repo is None:
        remote = current_repo()
        if remote and "/" in remote:
            r_owner, _, r_name = remote.partition("/")
            owner = owner or r_owner
            repo = repo or r_name
    if not owner or not repo:
        click.echo(
            "Error: could not resolve owner/repo. Pass --owner and --repo.",
            err=True,
        )
        sys.exit(2)

    if post_flag_set:
        _status(
            "Warning: --post has no effect in server-mode; the server posts by "
            "default. Use --no-post to suppress."
        )

    if ignored_flags:
        _status(
            "Warning: the following flags are ignored in server-mode: "
            + ", ".join(ignored_flags)
            + "."
        )

    config = load_config(config_path)
    fmt = output_format or config.format
    pass_list = _parse_passes(passes)
    poll_budget = float(timeout if timeout is not None else DEFAULT_TIMEOUT)

    try:
        result = review_via_server(
            server_url=server_url,
            server_key=server_key,
            owner=owner,
            repo=repo,
            pr_number=pr,
            passes=pass_list,
            post=post,
            poll_budget=poll_budget,
            on_status=_status,
        )
    except ServerReviewError as err:
        click.echo(f"Error: {err}", err=True)
        sys.exit(err.exit_code)

    if fmt == "json":
        click.echo(format_json(result))
    elif fmt == "markdown":
        click.echo(format_markdown(result))
    else:
        click.echo(format_table(result))

    for w in result.warnings:
        click.echo(f"\nWarning: {w}", err=True)

    if result.warnings:
        sys.exit(EXIT_PARTIAL_FAILURE)


@cli.command()
@click.option("--force", is_flag=True, help="Overwrite an existing .superseded.yaml")
@click.option(
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Path to write (default: .superseded.yaml in cwd)",
)
@click.pass_context
def init(ctx: click.Context, force: bool, config_path: Path | None) -> None:
    """Probe the environment and write a .superseded.yaml config file."""
    setup_logging(
        resolve_log_format(ctx.obj.get("log_format") if ctx.obj else None),
        resolve_log_level(ctx.obj.get("log_level") if ctx.obj else None),
    )
    _run_init(force=force, config_path=config_path)


def _run_init(force: bool, config_path: Path | None) -> None:
    target = config_path or Path(".superseded.yaml")

    if target.exists() and not force:
        _status(f"Error: {target} already exists. Use --force to overwrite.")
        sys.exit(2)

    if shutil.which("gh") is not None:
        _status("gh CLI: found")
    else:
        _status("gh CLI: not found (PR features will be disabled)")

    if (Path.cwd() / ".code-review-graph").is_dir():
        try:
            import code_review_graph  # noqa: F401

            _status("code-review-graph: found")
        except ImportError:
            _status("code-review-graph: graph dir present but package not installed")
    else:
        _status(
            "code-review-graph: not installed "
            "(graph-grounded reviews disabled; install with: "
            "uv add code-review-graph && code-review-graph build)"
        )

    key_status = {
        "deepseek": bool(os.environ.get("SUPERSEDED_DEEPSEEK_API_KEY")),
        "openai": bool(os.environ.get("SUPERSEDED_OPENAI_API_KEY")),
        "anthropic": bool(os.environ.get("SUPERSEDED_ANTHROPIC_API_KEY")),
    }
    if any(key_status.values()):
        _status("API keys: " + ", ".join(f"{k} {'✓' if v else '✗'}" for k, v in key_status.items()))
    else:
        _status(
            "API keys: none set — set one of SUPERSEDED_DEEPSEEK_API_KEY, "
            "SUPERSEDED_OPENAI_API_KEY, SUPERSEDED_ANTHROPIC_API_KEY."
        )

    server_url = os.environ.get("SUPERSEDED_SERVER_URL")
    server_key = os.environ.get("SUPERSEDED_SERVER_KEY")
    if server_url:
        _status(f"Review server: {server_url} (SUPERSEDED_SERVER_URL)")
        if not server_key:
            _status("  SUPERSEDED_SERVER_KEY not set — server-mode will need --server-key.")
    elif server_key:
        _status(
            "SUPERSEDED_SERVER_KEY set but SUPERSEDED_SERVER_URL is not — server-mode disabled."
        )

    cfg = Config(provider="deepseek")
    write_config(cfg, target)
    _status(f"Wrote {target} (provider: deepseek)")


@cli.command()
@click.option(
    "--database-url",
    "database_url",
    default=None,
    help="Database URL (default: local SQLite at .superseded/memory.db). Env: SUPERSEDED_DATABASE_URL.",
)
@click.pass_context
def migrate(ctx: click.Context, database_url: str | None) -> None:
    """Run database migrations to head and print the resulting revision."""
    from superseded.memory import alembic_runner

    setup_logging(
        resolve_log_format(ctx.obj.get("log_format") if ctx.obj else None),
        resolve_log_level(ctx.obj.get("log_level") if ctx.obj else None),
    )

    url = database_url or os.environ.get("SUPERSEDED_DATABASE_URL")
    if not url:
        url = f"sqlite:///{MemoryStore().db_path.resolve()}"

    rev = alembic_runner.upgrade(url)
    click.echo(rev)


@cli.command()
@click.option("--check", is_flag=True, help="Check for feedback on past reviews")
@click.option(
    "--pr",
    type=int,
    default=None,
    help="PR number to check for feedback (auto-detected from current branch if omitted)",
)
@click.option("--rules", is_flag=True, help="List learned review rules")
@click.option("--dismiss-rule", type=int, default=None, help="Dismiss a learned rule by ID")
@click.option("--helpful-rule", type=int, default=None, help="Reinforce a learned rule by ID")
@click.argument("comment_id", required=False)
@click.option("--helpful", is_flag=True)
@click.option("--dismiss", is_flag=True)
@click.pass_context
def feedback(
    ctx: click.Context,
    check: bool,
    pr: int | None,
    rules: bool,
    dismiss_rule: int | None,
    helpful_rule: int | None,
    comment_id: str | None,
    helpful: bool,
    dismiss: bool,
) -> None:
    """Manage review feedback."""
    setup_logging(
        resolve_log_format(ctx.obj.get("log_format") if ctx.obj else None),
        resolve_log_level(ctx.obj.get("log_level") if ctx.obj else None),
    )

    if rules:
        _run_feedback_rules()
        return

    if dismiss_rule is not None:
        _run_rule_action("dismiss", dismiss_rule)
        return

    if helpful_rule is not None:
        _run_rule_action("helpful", helpful_rule)
        return

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
@click.pass_context
def serve(ctx: click.Context, port: int | None, host: str | None, config_path: str | None) -> None:
    """Start the Superseded review server."""
    from contextlib import asynccontextmanager

    from superseded.memory.backend import make_store
    from superseded.server.config import ServerConfig
    from superseded.server.github import GitHubApp
    from superseded.server.repo_manager import RepoManager
    from superseded.server.worker import ReviewWorker

    config = ServerConfig.from_yaml(Path(config_path)) if config_path else ServerConfig.from_env()

    try:
        config.require_configured()
    except ValueError as err:
        click.echo(f"Error: {err}", err=True)
        sys.exit(2)

    key_env_by_provider = {
        "deepseek": "SUPERSEDED_DEEPSEEK_API_KEY",
        "openai": "SUPERSEDED_OPENAI_API_KEY",
        "anthropic": "SUPERSEDED_ANTHROPIC_API_KEY",
    }

    if config.provider not in key_env_by_provider:
        click.echo(f"Error: unknown provider {config.provider!r}", err=True)
        sys.exit(2)
    if not getattr(config, f"{config.provider}_api_key", None):
        click.echo(
            f"Error: {key_env_by_provider[config.provider]} must be set to serve.",
            err=True,
        )
        sys.exit(2)

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
    store = make_store(config.database_url, max_size=config.max_concurrent_reviews + 2)
    worker = ReviewWorker(
        github=github,
        repo_manager=repo_manager,
        max_concurrent=config.max_concurrent_reviews,
        store=store,
        server_provider=config.provider,
        server_model=config.model,
        server_reasoning_effort=config.reasoning_effort,
        provider=_build_server_provider(config),
    )

    import uvicorn

    from superseded.server.app import create_app
    from superseded.server.lifecycle import ServerLifecycle

    lifecycle = ServerLifecycle(worker=worker)

    @asynccontextmanager
    async def lifespan(_app):
        await lifecycle.startup()
        try:
            yield
        finally:
            await lifecycle.shutdown()
            with contextlib.suppress(Exception):
                await store.close()

    app = create_app(
        config=config,
        github=github,
        worker=worker,
        repo_manager=repo_manager,
        store=store,
        lifespan=lifespan,
    )

    serve_fmt = (
        os.environ.get(LOG_FORMAT_ENV) or (ctx.obj.get("log_format") if ctx.obj else None) or "json"
    )
    if _env_truthy(VERBOSE_ENV):
        serve_level = "DEBUG"
    else:
        serve_level = (
            os.environ.get(LOG_LEVEL_ENV)
            or (ctx.obj.get("log_level") if ctx.obj else None)
            or config.log_level
        )
    setup_logging(serve_fmt, serve_level)

    _status(f"Starting Superseded server on {config.host}:{config.port}")
    ssl_kwargs: dict = {}
    if config.tls_cert_path and config.tls_key_path:
        ssl_kwargs["ssl_certfile"] = str(config.tls_cert_path)
        ssl_kwargs["ssl_keyfile"] = str(config.tls_key_path)
        _status(f"TLS enabled: cert={config.tls_cert_path}")
    uvicorn.run(app, host=config.host, port=config.port, log_level=config.log_level, **ssl_kwargs)


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


def _run_feedback_rules() -> None:
    repo = current_repo()
    if repo is None:
        click.echo("Error: could not resolve current repository (is gh authenticated?).", err=True)
        sys.exit(1)
    store = MemoryStore()
    asyncio.run(store.init())
    rules = asyncio.run(store.get_all_learned_rules(repo))
    if not rules:
        click.echo("No learned rules found for this repository.")
        return
    for r in rules:
        applied = r.get("last_applied_at")
        applied_str = applied if applied else "never"
        click.echo(
            f"#{r['id']:>3d}  conf={r['confidence']:.2f}  applied={applied_str}"
            f"  evidence={r['evidence_count']}\n"
            f"      {r['rule_text']}"
        )


def _run_rule_action(action: str, rule_id: int) -> None:
    store = MemoryStore()
    asyncio.run(store.init())
    if action == "dismiss":
        ok = asyncio.run(store.dismiss_learned_rule(rule_id))
    else:
        ok = asyncio.run(store.reinforce_learned_rule(rule_id))
    if not ok:
        click.echo(f"Error: no learned rule found with id {rule_id}.", err=True)
        sys.exit(1)
    click.echo(f"Recorded {action} for rule {rule_id}.")
