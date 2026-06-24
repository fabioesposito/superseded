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

    return _parse_comment_lines(result.stdout)


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
