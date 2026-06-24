from __future__ import annotations

import json
import subprocess


def check_pr_feedback(pr: int, repo: str) -> list[dict]:
    try:
        result = subprocess.run(
            [
                "gh", "api",
                f"repos/{repo}/pulls/{pr}/comments",
                "--jq", ".[] | {id: .id, body: .body, path: .path, line: .line}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return []

    comments = []
    for line in result.stdout.strip().splitlines():
        if line:
            try:
                comments.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return comments
