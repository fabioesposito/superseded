from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from superseded.context.usage_retrieval import (  # noqa: F401
    _LANG_MAP,
    MAX_SYMBOLS,
    USAGE_BUDGET,
    extract_symbols,
)
from superseded.diff import parse_diff_files  # noqa: F401

logger = logging.getLogger(__name__)

_GRAPH_DIR = ".code-review-graph"
_REFRESH_TIMEOUT = 30


def is_available(root: Path) -> bool:
    """True iff code_review_graph imports AND a built graph exists."""
    try:
        import code_review_graph  # noqa: F401
    except ImportError:
        return False
    return (root / _GRAPH_DIR).is_dir()


def ensure_graph_fresh(root: Path) -> None:
    """Best-effort incremental graph refresh. Never raises."""
    try:
        subprocess.run(
            ["code-review-graph", "update", "--brief"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=_REFRESH_TIMEOUT,
        )
    except FileNotFoundError:
        logger.warning("code-review-graph CLI not on PATH; graph will be used as-is")
    except subprocess.TimeoutExpired:
        logger.warning(
            "code-review-graph update timed out after %ds; using stale graph",
            _REFRESH_TIMEOUT,
        )
    except OSError as err:
        logger.warning("code-review-graph update failed: %s", err)


def _query_callers(symbol: str, root: Path) -> list[str]:
    """Return a list of 'path:line: caller_name' strings, one per caller of `symbol`.

    Uses CRG's in-process query_graph API. Never raises — returns [] on any
    failure (not found, ambiguous, import error, query exception).
    """
    try:
        from code_review_graph.tools.query import query_graph
    except ImportError:
        return []

    try:
        result = query_graph(pattern="callers_of", target=symbol, repo_root=str(root))
    except ValueError as err:
        logger.warning("query_graph callers_of %s raised: %s", symbol, err)
        return []
    except Exception as err:  # sqlite corruption, etc.
        logger.warning("query_graph callers_of %s raised: %s", symbol, err)
        return []

    if result.get("status") != "ok":
        return []

    nodes = result.get("results") or []
    edges = result.get("edges") or []
    nodes_by_qn = {n.get("qualified_name"): n for n in nodes}

    lines: list[str] = []
    for edge in edges:
        if edge.get("kind") != "CALLS":
            continue
        file_path = edge.get("file_path") or ""
        line = edge.get("line") or ""
        caller_qn = edge.get("source") or ""
        node = nodes_by_qn.get(caller_qn)
        if node is not None:
            caller_name = node.get("name") or caller_qn
        else:
            caller_name = caller_qn.split("::")[-1] if "::" in caller_qn else caller_qn
        lines.append(f"{file_path}:{line}: {caller_name}")
    return lines
