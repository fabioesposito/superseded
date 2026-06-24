from __future__ import annotations

import json
import subprocess

from superseded.models import ReviewResult


def _escape_reasoning(reasoning: str) -> str:
    return reasoning.replace("<", "&lt;").replace(">", "&gt;")


def build_review_payload(result: ReviewResult) -> dict:
    comments = []
    for f in result.findings:
        body_text = f"**[{f.severity.upper()}] {f.title}** ({f.pass_name})\n\n{f.description}\n\n"
        if f.reasoning:
            body_text += (
                f"<details><summary>Reasoning</summary>\n\n"
                f"{_escape_reasoning(f.reasoning)}\n\n</details>\n\n"
            )
        body_text += f"**Suggestion:** {f.suggestion}"
        comment: dict = {
            "path": f.file,
            "line": f.end_line,
            "body": body_text,
        }
        if f.line != f.end_line:
            comment["start_line"] = f.line
        comments.append(comment)

    blocking = result.summary.get("critical", 0) + result.summary.get("important", 0)
    event = "REQUEST_CHANGES" if blocking > 0 else "COMMENT"

    passes_used = sorted({f.pass_name for f in result.findings})
    pass_labels = ", ".join(p.replace("_", " ").title() + " Review" for p in passes_used)

    body = "## Superseded Code Review\n\n"
    if pass_labels:
        body += f"**Passes:** {pass_labels}\n\n"
    for sev, count in result.summary.items():
        body += f"- **{sev}:** {count}\n"

    return {"event": event, "body": body, "comments": comments}


def _post_review_payload(payload: dict, target_repo: str, pr: int) -> list[int]:
    cmd = ["gh", "api", f"repos/{target_repo}/pulls/{pr}/reviews", "--input", "-"]
    response = subprocess.run(
        cmd, input=json.dumps(payload), text=True, capture_output=True, check=True
    )
    return _extract_comment_ids(response.stdout)


def post_review_to_pr(pr: int, result: ReviewResult, repo: str | None = None) -> list[int]:
    payload = build_review_payload(result)
    target_repo = repo if repo is not None else _repo()
    return _post_review_payload(payload, target_repo, pr)


def _extract_comment_ids(stdout: str) -> list[int]:
    if not stdout.strip():
        return []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    return [c["id"] for c in data.get("comments", []) if isinstance(c, dict) and "id" in c]


def _repo() -> str:
    result = subprocess.run(
        ["gh", "repo", "view", "--json", "owner,name", "-q", '.owner.login + "/" + .name'],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def current_repo() -> str | None:
    try:
        return _repo()
    except subprocess.CalledProcessError, FileNotFoundError:
        return None
