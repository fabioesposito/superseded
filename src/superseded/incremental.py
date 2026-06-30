from __future__ import annotations

import json
import subprocess

DEFAULT_TIMEOUT = 30
DIFF_ACCEPT = "application/vnd.github.v3.diff"


class IncrementalDiffError(RuntimeError):
    """Raised when the GitHub compare API call fails.

    Callers treat this as a signal to fall back to a full review.
    """


def fetch_incremental_diff(
    owner: str, repo: str, base_sha: str, head_sha: str
) -> tuple[str | None, str]:
    """Fetch the incremental diff between two commits via the GitHub compare API.

    Returns ``(diff, status)`` where ``status`` is one of ``"ahead"``,
    ``"identical"``, or ``"diverged"``. ``diff`` is the patch string when
    ``status == "ahead"`` and ``None`` otherwise. ``"behind"`` from the API is
    normalized to ``"diverged"``.

    Raises ``IncrementalDiffError`` on any ``gh``/network failure; callers fall
    back to a full review.
    """
    endpoint = f"repos/{owner}/{repo}/compare/{base_sha}...{head_sha}"
    try:
        status_result = subprocess.run(
            ["gh", "api", endpoint],
            capture_output=True,
            text=True,
            check=True,
            timeout=DEFAULT_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as err:
        raise IncrementalDiffError(f"gh api {endpoint} failed: {err}") from err

    try:
        payload = json.loads(status_result.stdout)
    except json.JSONDecodeError as err:
        raise IncrementalDiffError(f"compare response was not JSON: {err}") from err

    status = payload.get("status", "diverged")
    if status == "behind":
        status = "diverged"
    if status != "ahead":
        return None, status

    try:
        diff_result = subprocess.run(
            ["gh", "api", endpoint, "-H", f"Accept: {DIFF_ACCEPT}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=DEFAULT_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as err:
        raise IncrementalDiffError(f"gh api diff {endpoint} failed: {err}") from err
    return diff_result.stdout, "ahead"
