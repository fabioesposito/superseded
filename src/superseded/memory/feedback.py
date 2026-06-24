from __future__ import annotations

import json
import subprocess


def check_pr_feedback(pr: int, repo: str) -> list[dict]:
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repo}/pulls/{pr}/comments",
                "--jq",
                ".[] | {id: .id, body: .body, path: .path, line: .line, reactions: .reactions}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return []

    comments = _parse_comment_lines(result.stdout)

    owner, _, name = repo.partition("/")
    resolved_ids = check_resolved_threads(pr=pr, owner=owner, repo=name)
    if resolved_ids:
        for c in comments:
            if c.get("id") in resolved_ids:
                c["resolved"] = True

    return comments


def _parse_comment_lines(stdout: str) -> list[dict]:
    text = stdout.strip()
    if not text:
        return []
    comments: list[dict] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            comments.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if comments:
        return comments
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [c for c in data if isinstance(c, dict)]
    return []


def check_resolved_threads(pr: int, owner: str, repo: str) -> set[int]:
    """Return set of comment databaseIds for resolved review threads.

    Queries the GitHub GraphQL API for PullRequestReviewThread.isResolved,
    paginating via cursor until all threads are fetched. Returns an empty set
    on any error (graceful degradation — the reaction-based dismissal path
    still works).
    """
    query = (
        "query($owner:String!,$repo:String!,$pr:Int!,$cursor:String){"
        "repository(owner:$owner,name:$repo){"
        "pullRequest(number:$pr){"
        "reviewThreads(first:100,after:$cursor){"
        "nodes{isResolved,comments(first:1){nodes{databaseId}}},"
        "pageInfo{hasNextPage,endCursor}"
        "}}}}}"
    )

    resolved_ids: set[int] = set()
    cursor: str | None = None

    while True:
        cmd = [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-f",
            f"owner={owner}",
            "-f",
            f"repo={repo}",
            "-f",
            f"pr={pr}",
        ]
        if cursor is not None:
            cmd.extend(["-f", f"cursor={cursor}"])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError:
            return set()

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return set()

        threads = (
            data.get("data", {})
            .get("repository", {})
            .get("pullRequest", {})
            .get("reviewThreads", {})
        )
        if not threads:
            return set()

        for node in threads.get("nodes", []):
            if node.get("isResolved"):
                for comment in node.get("comments", {}).get("nodes", []):
                    cid = comment.get("databaseId")
                    if cid is not None:
                        resolved_ids.add(cid)

        page_info = threads.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if cursor is None:
            break

    return resolved_ids
