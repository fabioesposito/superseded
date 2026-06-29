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
