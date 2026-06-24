from __future__ import annotations

import json
import subprocess

from superseded.models import ReviewResult


def post_review_to_pr(pr: int, result: ReviewResult, repo: str | None = None) -> None:
    comments = []
    for f in result.findings:
        comments.append(
            {
                "path": f.file,
                "line": f.end_line,
                "body": (
                    f"**[{f.severity.upper()}] {f.title}** ({f.pass_name})\n\n"
                    f"{f.description}\n\n"
                    f"**Suggestion:** {f.suggestion}"
                ),
            }
        )

    event = "REQUEST_CHANGES" if result.summary.get("critical", 0) > 0 else "COMMENT"

    passes_used = sorted({f.pass_name for f in result.findings})
    pass_labels = ", ".join(p.replace("_", " ").title() + " Review" for p in passes_used)

    body = "## Superseded Code Review\n\n"
    if pass_labels:
        body += f"**Passes:** {pass_labels}\n\n"
    for sev, count in result.summary.items():
        body += f"- **{sev}:** {count}\n"

    payload = {
        "event": event,
        "body": body,
        "comments": comments,
    }

    target_repo = repo if repo is not None else _repo(pr)
    cmd = ["gh", "api", f"repos/{target_repo}/pulls/{pr}/reviews", "--input", "-"]
    subprocess.run(cmd, input=json.dumps(payload), text=True, check=True)


def _repo(pr: int) -> str:
    result = subprocess.run(
        ["gh", "repo", "view", "--json", "owner,name", "-q", '.owner.login + "/" + .name'],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()
