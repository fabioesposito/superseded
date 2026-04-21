from __future__ import annotations

from pathlib import Path

import frontmatter

from superseded.models import IssueStatus, Stage


def write_issue(filepath: str, content: str) -> None:
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def update_issue_status(filepath: str, status: IssueStatus, stage: Stage) -> None:
    path = Path(filepath)
    post = frontmatter.load(path)
    post["status"] = status.value
    post["stage"] = stage.value
    path.write_text(frontmatter.dumps(post))


def update_issue_body(filepath: str, body: str) -> None:
    path = Path(filepath)
    post = frontmatter.load(path)
    post.content = body
    path.write_text(frontmatter.dumps(post))


def delete_issue_file(filepath: str) -> None:
    path = Path(filepath)
    if path.exists():
        path.unlink()
