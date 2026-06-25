from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from superseded.context.conventions import discover_conventions
from superseded.context.spec_retrieval import discover_repo_specs
from superseded.context.static_analysis import run_static_analysis
from superseded.context.usage_retrieval import retrieve_usages
from superseded.diff import compute_file_context, parse_diff_files


def gather_context(
    diff: str,
    root: Path,
    *,
    static_analysis: bool = False,
    usage_retrieval: bool = False,
    conventions: bool = False,
    spec_retrieval: bool = False,
    extra_futures: dict[str, Future[Any] | None] | None = None,
    max_workers: int = 4,
) -> dict[str, str | None]:
    changed_files = (
        [e["file"] for e in parse_diff_files(diff)] if (static_analysis or usage_retrieval) else []
    )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures: dict[str, Future[Any] | None] = {
            "file_context": executor.submit(compute_file_context, diff, root=root),
            "static_signals": executor.submit(run_static_analysis, changed_files, root)
            if static_analysis
            else None,
            "usage_signals": executor.submit(retrieve_usages, diff, root)
            if usage_retrieval
            else None,
            "conventions_signals": executor.submit(discover_conventions, root)
            if conventions
            else None,
            "spec_signals": executor.submit(discover_repo_specs, diff, root)
            if spec_retrieval
            else None,
        }
        if extra_futures:
            futures.update(extra_futures)

        return {key: _get_result(future) for key, future in futures.items()}


def _get_result(future: Future[Any] | None) -> str | None:
    if future is None:
        return None
    val = future.result()
    return val or None


def submit_pr_description(
    executor: ThreadPoolExecutor, pr: int | None, fetch_fn: Callable[[int], str | None]
) -> Future[str | None] | None:
    if pr is None:
        return None
    return executor.submit(fetch_fn, pr)
