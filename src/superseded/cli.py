from __future__ import annotations

import asyncio
import sys

import click

from superseded.config import load_config
from superseded.diff import fetch_diff
from superseded.review.engine import ReviewEngine


@click.group()
@click.version_option(version="0.1.0")
def cli() -> None:
    """Superseded — multi-pass AI code review tool."""
    pass


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
@click.option("--post", is_flag=True, help="Post review to GitHub PR")
@click.option("--passes", default=None, help="Comma-separated passes to run")
def review(
    pr: int | None,
    diff_range: str | None,
    agent: str | None,
    model: str | None,
    output_format: str | None,
    post: bool,
    passes: str | None,
) -> None:
    """Review code changes."""
    if pr is None and diff_range is None:
        click.echo("Error: Either --pr or --diff must be provided.", err=True)
        sys.exit(1)

    _run_review(
        pr=pr,
        diff_range=diff_range,
        agent=agent,
        model=model,
        output_format=output_format,
        post=post,
        passes=passes,
    )


def _run_review(
    pr: int | None,
    diff_range: str | None,
    agent: str | None,
    model: str | None,
    output_format: str | None,
    post: bool,
    passes: str | None,
) -> None:
    config = load_config()
    agent_name = agent or config.agent
    model_name = model or config.model
    fmt = output_format or config.format

    click.echo("Fetching diff...")
    diff = fetch_diff(pr=pr, diff_range=diff_range)

    pass_list = passes.split(",") if passes else None

    click.echo(f"Running review with {agent_name}...")
    engine = ReviewEngine.select(agent_name, model=model_name)
    result = engine.review(diff=diff, passes=pass_list)

    from superseded.output.json_out import format_json
    from superseded.output.markdown import format_markdown
    from superseded.output.table import format_table

    if fmt == "json":
        click.echo(format_json(result))
    elif fmt == "markdown":
        click.echo(format_markdown(result))
    else:
        click.echo(format_table(result))

    if post and pr is not None:
        from superseded.output.github_pr import post_review_to_pr

        click.echo("Posting to GitHub PR...")
        post_review_to_pr(pr=pr, result=result)
        click.echo("Done.")


@cli.command()
@click.option("--check", is_flag=True, help="Check for feedback on past reviews")
@click.argument("comment_id", required=False)
@click.option("--helpful", is_flag=True)
@click.option("--dismiss", is_flag=True)
def feedback(check: bool, comment_id: str | None, helpful: bool, dismiss: bool) -> None:
    """Manage review feedback."""
    if check:
        click.echo("Checking for feedback on past reviews...")
        from superseded.memory.store import MemoryStore

        store = MemoryStore()
        asyncio.run(store.init())
        click.echo("Use: superseded feedback <comment-id> --helpful/--dismiss")
        return

    if comment_id and (helpful or dismiss):
        action = "helpful" if helpful else "dismiss"
        click.echo(f"Recording {action} for {comment_id}...")
        from superseded.memory.store import MemoryStore

        store = MemoryStore()
        asyncio.run(store.init())
        asyncio.run(store.record_feedback(comment_id, action))
        click.echo(f"Recorded {action} for {comment_id}.")
        return

    click.echo("Usage: superseded feedback --check OR superseded feedback <id> --helpful/--dismiss")
