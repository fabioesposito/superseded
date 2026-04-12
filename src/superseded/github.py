from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass


@dataclass
class GhComment:
    author: str
    body: str
    created_at: str


@dataclass
class GhIssue:
    title: str
    body: str
    labels: list[str]
    assignee: str
    comments: list[GhComment]
    url: str


GITHUB_ISSUE_URL_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/issues/(\d+)")


def parse_github_url(url: str) -> tuple[str, str, int]:
    match = GITHUB_ISSUE_URL_RE.match(url.strip())
    if not match:
        raise ValueError(
            "Invalid GitHub issue URL. Expected: https://github.com/owner/repo/issues/123"
        )
    owner, repo, number = match.groups()
    return owner, repo, int(number)


async def fetch_github_issue(url: str) -> GhIssue:
    owner, repo, number = parse_github_url(url)

    proc = await asyncio.create_subprocess_exec(
        "gh",
        "issue",
        "view",
        str(number),
        "--repo",
        f"{owner}/{repo}",
        "--json",
        "title,body,labels,assignee,comments",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(stderr.decode().strip())

    data = json.loads(stdout)

    labels = [label["name"] for label in data.get("labels", [])]
    assignee = ""
    if data.get("assignee") and data["assignee"].get("login"):
        assignee = data["assignee"]["login"]

    comments = []
    for c in data.get("comments", []):
        author = c.get("author", {}).get("login", "unknown")
        comments.append(
            GhComment(
                author=author,
                body=c.get("body", ""),
                created_at=c.get("createdAt", ""),
            )
        )

    return GhIssue(
        title=data.get("title", ""),
        body=data.get("body", ""),
        labels=labels,
        assignee=assignee,
        comments=comments,
        url=url,
    )


def format_description(body: str, comments: list[GhComment]) -> str:
    parts = [body] if body else []
    for c in comments:
        date_str = c.created_at[:10] if c.created_at else ""
        parts.append(f"---\n**@{c.author}** ({date_str}):\n\n{c.body}")
    return "\n\n".join(parts)
