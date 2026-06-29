from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from superseded.context import graph_retrieval
from superseded.context.conventions import discover_conventions
from superseded.context.spec_retrieval import discover_repo_specs
from superseded.context.static_analysis import run_static_analysis
from superseded.context.usage_retrieval import retrieve_usages
from superseded.diff import compute_file_context, parse_diff_files


def _refresh_then_retrieve(diff: str, root: Path, changed_files: list[str]) -> str | None:
    """Refresh the graph then query it. Runs sequentially in one worker thread
    so the refresh completes before any query reads the graph, while other
    context futures continue in parallel."""
    graph_retrieval.ensure_graph_fresh(root)
    return graph_retrieval.retrieve_usages_via_graph(diff, root, changed_files=changed_files)


def gather_context(
    diff: str,
    root: Path,
    *,
    static_analysis: bool = False,
    usage_retrieval: bool = False,
    conventions: bool = False,
    spec_retrieval: bool = False,
    graph: bool = False,
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
            "usage_signals": _submit_usage(
                executor, diff, root, changed_files, usage_retrieval, graph
            ),
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


def _submit_usage(
    executor: ThreadPoolExecutor,
    diff: str,
    root: Path,
    changed_files: list[str],
    usage_retrieval: bool,
    graph: bool,
) -> Future[str | None] | None:
    if not usage_retrieval:
        return None
    if graph and graph_retrieval.is_available(root):
        return executor.submit(_refresh_then_retrieve, diff, root, changed_files)
    return executor.submit(retrieve_usages, diff, root)


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
