from __future__ import annotations

import json
import re
import subprocess

from superseded.diff import _HUNK_RE, DEFAULT_GH_TIMEOUT, parse_diff_files
from superseded.models import Finding, ReviewResult

# Cap per-comment bodies so a runaway agent can't dump an unbounded amount of
# (potentially secret-bearing) text into a PR comment. GitHub's own review
# comment limit is well above this; the cap is a defense-in-depth sanity bound.
MAX_COMMENT_CHARS = 20_000

# Secret shapes that may appear in diff/file context the agent quotes back.
# Matched anywhere in comment bodies before posting; replaced with [REDACTED].
_SECRET_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"
    r"|AKIA[0-9A-Z]{16}"
    r"|sk-(?:ant-)?[A-Za-z0-9_-]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{36,}"
    r"|github_pat_[A-Za-z0-9_]{22,}"
    r"|glpat-[A-Za-z0-9_-]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|Bearer [A-Za-z0-9._-]{16,}"
)


def _redact(text: str) -> str:
    """Replace common credential patterns with ``[REDACTED]``."""
    return _SECRET_RE.sub("[REDACTED]", text)


def _truncate(text: str, limit: int = MAX_COMMENT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n…(comment truncated)"


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
            "body": _truncate(_redact(body_text)),
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
        cmd,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
        timeout=DEFAULT_GH_TIMEOUT,
    )
    return _extract_comment_ids(response.stdout)


def _file_hunk_windows(diff: str) -> dict[str, list[tuple[int, int]]]:
    """Map each changed file to its inclusive new-side line windows.

    Each window is ``(start, end)`` covering the hunk's counted new lines. A
    hunk ``@@ -a,b +c,d @@`` covers new lines ``[c, c+d-1]`` (d defaults to 1
    when omitted). Hunks with ``d == 0`` produce an empty window and are skipped.
    """
    windows: dict[str, list[tuple[int, int]]] = {}
    for entry in parse_diff_files(diff):
        file_path = entry["file"]
        wins: list[tuple[int, int]] = []
        for m in _HUNK_RE.finditer(entry["diff"]):
            start = int(m.group(2))
            count = int(m.group(3)) if m.group(3) is not None else 1
            if count <= 0:
                continue
            wins.append((start, start + count - 1))
        windows[file_path] = wins
    return windows


def _line_in_any_window(line: int, windows: list[tuple[int, int]]) -> bool:
    return any(start <= line <= end for start, end in windows)


def _out_of_range_indices(comments: list[dict], diff: str) -> set[int]:
    """Return indices of comments whose line range falls outside the diff hunks.

    A comment is in-range when both its ``start_line`` (if present) and ``line``
    land within the same file's hunk window. When *diff* is empty/whitespace
    no validation is possible and every comment is treated as in-range (the
    caller opts into validation by supplying the diff).
    """
    if not diff or not diff.strip() or not comments:
        return set()
    windows = _file_hunk_windows(diff)
    bad: set[int] = set()
    for i, c in enumerate(comments):
        path = c.get("path", "")
        wins = windows.get(path)
        if not wins:
            bad.add(i)
            continue
        line = int(c.get("line", 0))
        start_line = c.get("start_line")
        if start_line is not None:
            if not _line_in_any_window(int(start_line), wins) or not _line_in_any_window(
                line, wins
            ):
                bad.add(i)
        elif not _line_in_any_window(line, wins):
            bad.add(i)
    return bad


def _build_fallback_text(findings: list[Finding]) -> str:
    lines = [
        "\n\n## Out-of-range findings\n\n",
        "These findings could not be placed as inline comments because their ",
        "line numbers fall outside the PR diff hunk:\n\n",
    ]
    for f in findings:
        lines.append(f"- **{f.file}:{f.line}** [{f.severity}] {_redact(f.title)}\n")
    return "".join(lines)


def post_review_to_pr(
    pr: int, result: ReviewResult, repo: str | None = None, diff: str = ""
) -> list[int | None]:
    """Post a review with inline comments, demoting out-of-range findings to body text.

    Validation is local: each finding's line range is checked against the diff
    hunks *before* any GitHub API call. In-range findings are posted in a single
    review; out-of-range findings are appended to the review body as a fallback
    list and posted in one additional body-only review. If GitHub rejects the
    pre-filtered good batch anyway (e.g. a stale diff), the entire set is demoted
    to the body — never probed recursively.
    """
    payload = build_review_payload(result)
    target_repo = repo if repo is not None else _repo()
    comments = payload["comments"]

    if not comments:
        _post_review_payload(payload, target_repo, pr)
        return []

    bad_indices = _out_of_range_indices(comments, diff)
    good_indices = [i for i in range(len(comments)) if i not in bad_indices]
    comment_ids: list[int | None] = [None] * len(comments)

    good_comments = [comments[i] for i in good_indices]
    if good_comments:
        try:
            ids = _post_review_payload({**payload, "comments": good_comments}, target_repo, pr)
        except subprocess.CalledProcessError:
            if not bad_indices:
                raise
            # Stale diff / race: demote everything to the fallback body.
            bad_indices = set(range(len(comments)))
            good_indices = []
        else:
            for j, idx in enumerate(good_indices):
                if j < len(ids):
                    comment_ids[idx] = ids[j]

    bad_findings = [result.findings[i] for i in sorted(bad_indices)]
    if bad_findings:
        payload["body"] += _build_fallback_text(bad_findings)
        payload["comments"] = []
        _post_review_payload(payload, target_repo, pr)

    return comment_ids


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
        timeout=DEFAULT_GH_TIMEOUT,
    )
    return result.stdout.strip()


def current_repo() -> str | None:
    try:
        return _repo()
    except subprocess.CalledProcessError, FileNotFoundError:
        return None
