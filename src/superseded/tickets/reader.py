from __future__ import annotations

from pathlib import Path

import frontmatter

from superseded.models import Issue, IssueStatus, Stage


def read_issue(filepath: str) -> Issue:
    path = Path(filepath)
    post = frontmatter.load(path)
    return Issue(
        id=post.get("id", "SUP-000"),
        title=post.get("title", "Untitled"),
        status=IssueStatus(post.get("status", "new")),
        stage=Stage(post.get("stage", "spec")),
        created=post.get("created", ""),
        assignee=post.get("assignee", ""),
        labels=post.get("labels", []),
        filepath=str(path),
        repos=post.get("repos", []),
    )


def list_issues(issues_dir: str) -> list[Issue]:
    path = Path(issues_dir)
    if not path.exists():
        return []
    issues: list[Issue] = []
    for md_file in sorted(path.glob("*.md")):
        issues.append(read_issue(str(md_file)))
    return issues
