from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT_PADDING = 20
DEFAULT_GH_TIMEOUT = 30


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
            timeout=DEFAULT_GH_TIMEOUT,
        )
    except FileNotFoundError as err:
        raise RuntimeError(
            "'gh' CLI not found on PATH. Install it: https://cli.github.com/"
        ) from err
    except subprocess.CalledProcessError as err:
        detail = (err.stderr or "").strip()
        msg = f"'gh pr diff {pr}' failed (exit {err.returncode})"
        if detail:
            msg += f": {detail}"
        raise RuntimeError(msg) from err
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
            timeout=DEFAULT_GH_TIMEOUT,
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
            timeout=DEFAULT_GH_TIMEOUT,
        )
    except subprocess.CalledProcessError, FileNotFoundError:
        return None
    body = result.stdout.strip()
    return body or None


def fetch_pr_head_sha(pr: int) -> str:
    """Return the current HEAD SHA of PR ``pr`` via ``gh pr view``."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr), "--json", "headRefOid", "-q", ".headRefOid"],
            capture_output=True,
            text=True,
            check=True,
            timeout=DEFAULT_GH_TIMEOUT,
        )
    except FileNotFoundError as err:
        raise RuntimeError(
            "'gh' CLI not found on PATH. Install it: https://cli.github.com/"
        ) from err
    except subprocess.CalledProcessError as err:
        detail = (err.stderr or "").strip()
        msg = f"'gh pr view {pr}' failed (exit {err.returncode})"
        if detail:
            msg += f": {detail}"
        raise RuntimeError(msg) from err
    return result.stdout.strip()


_PLUS_RE = re.compile(r"^\+\+\+ b/(.+?)\s*$", re.MULTILINE)
_PLUS_QUOTED_RE = re.compile(r'^\+\+\+ "b/(.+?)"\s*$', re.MULTILINE)
_MINUS_RE = re.compile(r"^--- a/(.+?)\s*$", re.MULTILINE)
_MINUS_QUOTED_RE = re.compile(r'^--- "a/(.+?)"\s*$', re.MULTILINE)
# Blocks are split on `diff --git `, so the block body begins with `a/<old> b/<new>`.
_HEADER_RE = re.compile(r"^a/.+? b/(.+?)\s*$", re.MULTILINE)
_HEADER_QUOTED_RE = re.compile(r'^"a/.+?" "b/(.+?)"\s*$', re.MULTILINE)


def _unescape_git_path(s: str) -> str:
    """Reverse git's C-style path quoting (\" \\\\ \\t \\n are the common escapes)."""
    return s.replace('\\"', '"').replace("\\\\", "\\").replace("\\t", "\t").replace("\\n", "\n")


def _extract_path(block: str) -> str | None:
    """Pick the canonical filesystem path for a diff block.

    Prefers the new side (``+++ b/<path>``), which is correct for renames and
    additions; falls back to the old side for deletions (``+++ /dev/null``).
    When the block has no ``+++``/``---`` lines (binary files), parses the
    ``diff --git a/<old> b/<new>`` header for the new path. Handles quoted
    paths containing spaces or special characters.
    """
    m = _PLUS_RE.search(block)
    if m and m.group(1) != "/dev/null":
        return m.group(1)
    m = _PLUS_QUOTED_RE.search(block)
    if m:
        return _unescape_git_path(m.group(1))
    m = _MINUS_RE.search(block)
    if m and m.group(1) != "/dev/null":
        return m.group(1)
    m = _MINUS_QUOTED_RE.search(block)
    if m:
        return _unescape_git_path(m.group(1))
    m = _HEADER_RE.search(block)
    if m:
        return m.group(1)
    m = _HEADER_QUOTED_RE.search(block)
    if m:
        return _unescape_git_path(m.group(1))
    return None


def parse_diff_files(diff: str) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    parts = re.split(r"^diff --git ", diff, flags=re.MULTILINE)
    for part in parts:
        if not part.strip():
            continue
        path = _extract_path(part)
        if path:
            files.append({"file": path, "diff": "diff --git " + part})
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
            if root is not None:
                resolved = full.resolve()
                root_resolved = root.resolve()
                if not resolved.is_relative_to(root_resolved):
                    logger.debug("Skipping file outside repo root: %s", file_path)
                    continue
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
            timeout=DEFAULT_GH_TIMEOUT,
        )
        return Path(result.stdout.strip())
    except subprocess.CalledProcessError, FileNotFoundError:
        return Path.cwd()
