from __future__ import annotations

import re
import subprocess


def fetch_diff(pr: int | None = None, diff_range: str | None = None) -> str:
    if pr is not None:
        return _fetch_pr_diff(pr)
    if diff_range is not None:
        return _fetch_git_diff(diff_range)
    raise ValueError("Either --pr or --diff must be provided")


def _fetch_pr_diff(pr: int) -> str:
    result = subprocess.run(
        ["gh", "pr", "diff", str(pr)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _fetch_git_diff(diff_range: str) -> str:
    result = subprocess.run(
        ["git", "diff", diff_range],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def parse_diff_files(diff: str) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    parts = re.split(r"^diff --git ", diff, flags=re.MULTILINE)
    for part in parts:
        if not part.strip():
            continue
        match = re.search(r"a/(.+?) b/", part)
        if match:
            files.append({"file": match.group(1), "diff": "diff --git " + part})
    return files
