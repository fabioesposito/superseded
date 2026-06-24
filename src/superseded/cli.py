from __future__ import annotations

import click


@click.group()
@click.version_option()
def cli() -> None:
    """Superseded — multi-pass AI code review tool."""
    pass


@cli.command()
@click.option("--pr", type=int, help="PR number to review")
@click.option("--diff", "diff_range", help="Git diff range (e.g. HEAD~3..HEAD)")
@click.option("--agent", default=None, help="AI CLI agent (claude-code, opencode, codex)")
@click.option("--model", default=None, help="Model to use")
@click.option("--format", "output_format", type=click.Choice(["json", "markdown", "table"]), default="table")
@click.option("--post", is_flag=True, help="Post review to GitHub PR")
@click.option("--passes", default=None, help="Comma-separated passes to run")
def review(pr, diff_range, agent, model, output_format, post, passes):
    """Review code changes."""
    click.echo("Not yet implemented")


@cli.command()
@click.option("--check", is_flag=True, help="Check for feedback on past reviews")
@click.argument("comment_id", required=False)
@click.option("--helpful", is_flag=True)
@click.option("--dismiss", is_flag=True)
def feedback(check, comment_id, helpful, dismiss):
    """Manage review feedback."""
    click.echo("Not yet implemented")
