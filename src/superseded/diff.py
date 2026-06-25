from __future__ import annotations

import re
import subprocess
from pathlib import Path

DEFAULT_CONTEXT_PADDING = 20


def fetch_diff(
    pr: int | None = None,
    diff_range: str | None = None,
    files: list[str] | None = None,
) -> str:
    """Fetch a diff.

    ``pr`` and ``diff_range`` are mutually exclusive. ``files`` restricts a
    local ``--diff`` to the given pathspecs (cannot be combined with ``--pr``);
    when only ``files`` are given, the diff defaults to the working tree vs
    ``HEAD``.
    """
    if pr is not None:
        if files:
            raise ValueError("positional FILES cannot be combined with --pr")
        return _fetch_pr_diff(pr)
    if diff_range is not None or files:
        rng = diff_range or "HEAD"
        return _fetch_git_diff(rng, files)
    raise ValueError("Either --pr, --diff, or FILES must be provided")


def _fetch_pr_diff(pr: int) -> str:
    try:
        result = subprocess.run(
            ["gh", "pr", "diff", str(pr)],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as err:
        raise RuntimeError(
            "'gh' CLI not found on PATH. Install it: https://cli.github.com/"
        ) from err
    return result.stdout


def _fetch_git_diff(diff_range: str, files: list[str] | None = None) -> str:
    cmd = ["git", "diff", diff_range]
    if files:
        cmd += ["--", *files]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as err:
        raise RuntimeError("'git' not found on PATH. Install git to use --diff.") from err
    return result.stdout


def fetch_pr_description(pr: int) -> str | None:
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr), "--json", "body", "-q", ".body"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError, FileNotFoundError:
        return None
    body = result.stdout.strip()
    return body or None


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


def _read_file_lines(path: str) -> list[str]:
    return Path(path).read_text().splitlines()


_HUNK_RE = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


def compute_file_context(
    diff: str, context_padding: int = DEFAULT_CONTEXT_PADDING, root: Path | None = None
) -> str:
    blocks: list[str] = []
    for entry in parse_diff_files(diff):
        file_path = entry["file"]
        hunks = list(_HUNK_RE.finditer(entry["diff"]))
        if not hunks:
            continue
        new_starts = sorted({int(m.group(2)) for m in hunks})
        try:
            full = root / file_path if root is not None else Path(file_path)
            lines = _read_file_lines(str(full))
        except FileNotFoundError, OSError:
            continue
        snippets: list[str] = []
        for start in new_starts:
            idx = max(0, start - 1 - context_padding)
            end = min(len(lines), start + context_padding)
            window = lines[idx:end]
            snippet = "\n".join(f"{idx + i + 1}: {line}" for i, line in enumerate(window))
            snippets.append(f"# {file_path} around line {start}\n{snippet}")
        if snippets:
            blocks.append("\n\n".join(snippets))
    return "\n\n".join(blocks)


def repo_root() -> Path:
    """Return the git repo root, falling back to cwd."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except subprocess.CalledProcessError, FileNotFoundError:
        return Path.cwd()
