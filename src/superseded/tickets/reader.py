from __future__ import annotations

from pathlib import Path

from superseded.models import Issue


def read_issue(filepath: str) -> Issue:
    path = Path(filepath)
    content = path.read_text(encoding="utf-8")
    return Issue.from_frontmatter(content, filepath=str(path))


def list_issues(issues_dir: str) -> list[Issue]:
    path = Path(issues_dir)
    if not path.exists():
        return []
    issues: list[Issue] = []
    for md_file in sorted(path.glob("*.md")):
        issues.append(read_issue(str(md_file)))
    return issues
