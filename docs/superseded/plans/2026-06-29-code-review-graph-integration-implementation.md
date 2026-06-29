# code-review-graph Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `rg`-based caller lookup in `context/usage_retrieval.py` with a graph-aware equivalent backed by `code-review-graph` (CRG) when CRG is installed and a built graph exists, while transparently falling back to the existing `rg` path otherwise.

**Architecture:** A new `context/graph_retrieval.py` module wraps CRG's in-process `query_graph` API (verified: `from code_review_graph.tools.query import query_graph` returns `{status, results, edges}` with no exceptions on not-found). `gathering.py` gains a `graph: bool` flag that, when true, refreshes the graph via `code-review-graph update` and routes usage retrieval through CRG when `is_available` is true; otherwise the rg path runs unchanged. Config + CLI mirror the existing `conventions` / `spec_retrieval` toggle pattern exactly (env > flag > config precedence).

**Tech Stack:** Python 3.14+, pydantic v2, click, optional dep on `code-review-graph` (PyPI). No new required deps.

**Spec:** `docs/superseded/specs/2026-06-29-code-review-graph-integration-design.md`

---

## File map

**Create:**
- `src/superseded/context/graph_retrieval.py` — CRG probe + graph refresh + per-symbol callers query. Public functions: `is_available(root)`, `ensure_graph_fresh(root)`, `retrieve_usages_via_graph(diff, root, changed_files=None)`. Private helper `_query_callers(symbol, root)`.
- `tests/test_graph_retrieval.py` — unit tests for the above with mocked `code_review_graph` import + mocked `query_graph` function.
- `tests/test_gathering_graph.py` — tests for the gathering.py `graph=` flag wiring (graph path, rg fallback, refresh-before-query ordering).

**Modify:**
- `src/superseded/config.py` — add `graph: bool = True` to `Config`.
- `src/superseded/detection.py` — add `detect_code_review_graph(root) -> bool`.
- `src/superseded/cli.py` — add `--graph`/`--no-graph` flag, `GRAPH_ENV`, `resolve_graph()` helper, thread resolved value through `_run_review` into `gather_context`.
- `src/superseded/context/gathering.py` — add `graph: bool = False` param; refresh-then-query path; rg fallback when graph unavailable.
- `pyproject.toml` — add `[project.optional-dependencies] graph = ["code-review-graph"]`.
- `tests/test_config.py` — `graph` default + round-trip.
- `tests/test_detection.py` — `detect_code_review_graph` cases.
- `tests/test_cli.py` — `resolve_graph` precedence; `--graph`/`--no-graph` propagation to `gather_context`.
- `tests/test_init.py` — `init` prints CRG status line.

---

## Task 1: Add `graph` config field

**Files:**
- Modify: `src/superseded/config.py:18-28`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_config_graph_default_true():
    from superseded.config import Config

    assert Config().graph is True


def test_config_graph_round_trip(tmp_path):
    from superseded.config import Config, load_config, write_config

    cfg = Config(graph=False)
    target = tmp_path / ".superseded.yaml"
    write_config(cfg, target)
    loaded = load_config(target)
    assert loaded.graph is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_config_graph_default_true -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'graph'` (or pydantic validation error).

- [ ] **Step 3: Add the field**

Edit `src/superseded/config.py` — extend the `Config` class so the boolean fields are grouped at the end:

```python
class Config(BaseModel):
    agent: str = "opencode"
    model: str | None = "deepseek-v4-pro"
    passes: PassConfig = PassConfig()
    post_to_pr: bool = False
    format: str = "table"
    memory: bool = True
    static_analysis: bool = True
    usage_retrieval: bool = True
    conventions: bool = True
    spec_retrieval: bool = True
    graph: bool = True

    def is_pass_enabled(self, name: str) -> bool:
        return getattr(self.passes, name, False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS including both new tests.

- [ ] **Step 5: Commit**

```bash
git add src/superseded/config.py tests/test_config.py
git commit -m "feat(config): add graph toggle (default true)"
```

---

## Task 2: Add CRG detection helper

**Files:**
- Modify: `src/superseded/detection.py:34` (after `detect_gh`)
- Test: `tests/test_detection.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_detection.py`:

```python
def test_detect_code_review_graph_false_when_import_missing(monkeypatch):
    """When code_review_graph can't be imported, detection returns False even if
    a .code-review-graph/ dir exists."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "code_review_graph":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.mkdir(Path("/repo/.code-review-graph"))  # type: ignore[arg-type]
    from superseded.detection import detect_code_review_graph

    assert detect_code_review_graph(Path("/repo")) is False


def test_detect_code_review_graph_false_when_dir_missing(tmp_path, monkeypatch):
    """Importable but no built graph dir -> False."""
    # We can't actually install code_review_graph here; instead mock the import
    # to succeed by injecting a dummy module in sys.modules.
    import sys
    from types import ModuleType

    monkeypatch.setitem(sys.modules, "code_review_graph", ModuleType("code_review_graph"))
    from superseded.detection import detect_code_review_graph

    assert detect_code_review_graph(tmp_path) is False


def test_detect_code_review_graph_true(tmp_path, monkeypatch):
    """Importable AND built graph dir -> True."""
    import sys
    from types import ModuleType

    monkeypatch.setitem(sys.modules, "code_review_graph", ModuleType("code_review_graph"))
    (tmp_path / ".code-review-graph").mkdir()
    from superseded.detection import detect_code_review_graph

    assert detect_code_review_graph(tmp_path) is True
```

Make sure `from pathlib import Path` is present at the top of `tests/test_detection.py`; if not, add it.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_detection.py::detect_code_review_graph -v`
Expected: FAIL with `ImportError: cannot import name 'detect_code_review_graph'`.

- [ ] **Step 3: Implement the helper**

Edit `src/superseded/detection.py` — add to the end of the file:

```python
def detect_code_review_graph(root: Path) -> bool:
    """True iff code_review_graph imports AND a built graph exists at <root>/.code-review-graph."""
    try:
        import code_review_graph  # noqa: F401
    except ImportError:
        return False
    return (root / ".code-review-graph").is_dir()
```

Also add `from pathlib import Path` at the top of `detection.py` (currently missing — the file imports `shutil` and `dataclass` but not `Path`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_detection.py -v`
Expected: PASS including the three new tests.

- [ ] **Step 5: Commit**

```bash
git add src/superseded/detection.py tests/test_detection.py
git commit -m "feat(detection): add detect_code_review_graph"
```

---

## Task 3: Create `graph_retrieval.py` skeleton with `is_available`

**Files:**
- Create: `src/superseded/context/graph_retrieval.py`
- Test: `tests/test_graph_retrieval.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_graph_retrieval.py`:

```python
from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock


def test_is_available_false_when_import_missing(tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "code_review_graph":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    from superseded.context.graph_retrieval import is_available

    assert is_available(tmp_path) is False


def test_is_available_false_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "code_review_graph", ModuleType("code_review_graph"))
    from superseded.context.graph_retrieval import is_available

    assert is_available(tmp_path) is False


def test_is_available_true(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "code_review_graph", ModuleType("code_review_graph"))
    (tmp_path / ".code-review-graph").mkdir()
    from superseded.context.graph_retrieval import is_available

    assert is_available(tmp_path) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_graph_retrieval.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'superseded.context.graph_retrieval'`.

- [ ] **Step 3: Implement `is_available`**

Create `src/superseded/context/graph_retrieval.py`:

```python
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from superseded.context.usage_retrieval import (
    USAGE_BUDGET,
    _LANG_MAP,
    MAX_SYMBOLS,
    extract_symbols,
)
from superseded.diff import parse_diff_files

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_graph_retrieval.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/superseded/context/graph_retrieval.py tests/test_graph_retrieval.py
git commit -m "feat(graph_retrieval): add is_available probe"
```

---

## Task 4: Add `ensure_graph_fresh`

**Files:**
- Modify: `src/superseded/context/graph_retrieval.py`
- Test: `tests/test_graph_retrieval.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_graph_retrieval.py`:

```python
def test_ensure_graph_fresh_swallows_file_not_found(monkeypatch, caplog):
    import subprocess

    def boom(*a, **kw):
        raise FileNotFoundError("no code-review-graph")

    monkeypatch.setattr(subprocess, "run", boom)
    from superseded.context.graph_retrieval import ensure_graph_fresh

    with caplog.at_level("WARNING"):
        ensure_graph_fresh(Path("/repo"))  # must not raise
    assert "code-review-graph" in caplog.text


def test_ensure_graph_fresh_swallows_timeout(monkeypatch, caplog):
    import subprocess

    def boom(*a, **kw):
        raise subprocess.TimeoutExpired(cmd=["code-review-graph"], timeout=30)

    monkeypatch.setattr(subprocess, "run", boom)
    from superseded.context.graph_retrieval import ensure_graph_fresh

    with caplog.at_level("WARNING"):
        ensure_graph_fresh(Path("/repo"))  # must not raise
    assert "timed out" in caplog.text


def test_ensure_graph_fresh_swallows_oserror(monkeypatch, caplog):
    import subprocess

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(subprocess, "run", boom)
    from superseded.context.graph_retrieval import ensure_graph_fresh

    with caplog.at_level("WARNING"):
        ensure_graph_fresh(Path("/repo"))  # must not raise


def test_ensure_graph_fresh_passes_cwd(monkeypatch):
    import subprocess

    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    from superseded.context.graph_retrieval import ensure_graph_fresh

    ensure_graph_fresh(Path("/repo"))
    assert seen["cmd"] == ["code-review-graph", "update", "--brief"]
    assert seen["kwargs"]["cwd"] == Path("/repo")
    assert seen["kwargs"]["timeout"] == 30
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_graph_retrieval.py::test_ensure_graph_fresh_passes_cwd -v`
Expected: FAIL with `AttributeError: ... has no attribute 'ensure_graph_fresh'`.

- [ ] **Step 3: Implement `ensure_graph_fresh`**

Append to `src/superseded/context/graph_retrieval.py`:

```python
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
        logger.warning(
            "code-review-graph CLI not on PATH; graph will be used as-is"
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "code-review-graph update timed out after %ds; using stale graph",
            _REFRESH_TIMEOUT,
        )
    except OSError as err:
        logger.warning("code-review-graph update failed: %s", err)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_graph_retrieval.py -v`
Expected: PASS (7 tests total now).

- [ ] **Step 5: Commit**

```bash
git add src/superseded/context/graph_retrieval.py tests/test_graph_retrieval.py
git commit -m "feat(graph_retrieval): add ensure_graph_fresh"
```

---

## Task 5: Add `_query_callers` (CRG API integration)

**Files:**
- Modify: `src/superseded/context/graph_retrieval.py`
- Test: `tests/test_graph_retrieval.py`

The CRG API contract (verified by reading installed source): `query_graph(pattern="callers_of", target="sym", repo_root=None)` returns a dict `{"status": "ok"|"not_found"|"ambiguous"|"error", "results": [...nodes...], "edges": [...edges...]}`. Each node carries `file_path`, `line_start`, `name`, `qualified_name`. Each edge carries `kind`, `source` (caller qualified name), `file_path`, `line` (call-site line). No exceptions on not-found; `ValueError` only on bad explicit `repo_root`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_graph_retrieval.py`:

```python
def test_query_callers_formats_path_line_name(monkeypatch):
    """Each caller becomes a 'path:line: caller_name' line (matches rg -n output)."""
    fake_result = {
        "status": "ok",
        "results": [
            {
                "qualified_name": "src/caller.py::do_thing",
                "name": "do_thing",
                "file_path": "src/caller.py",
                "line_start": 42,
            }
        ],
        "edges": [
            {
                "kind": "CALLS",
                "source": "src/caller.py::do_thing",
                "target": "src/lib.py::foobar",
                "file_path": "src/caller.py",
                "line": 45,
            }
        ],
    }

    fake_query_graph = MagicMock(return_value=fake_result)
    fake_module = ModuleType("code_review_graph")
    fake_tools = ModuleType("code_review_graph.tools")
    fake_query_mod = ModuleType("code_review_graph.tools.query")
    fake_query_mod.query_graph = fake_query_graph
    fake_tools.query = fake_query_mod
    fake_module.tools = fake_tools
    monkeypatch.setitem(sys.modules, "code_review_graph", fake_module)
    monkeypatch.setitem(sys.modules, "code_review_graph.tools", fake_tools)
    monkeypatch.setitem(sys.modules, "code_review_graph.tools.query", fake_query_mod)

    from superseded.context.graph_retrieval import _query_callers

    lines = _query_callers("foobar", Path("/repo"))
    assert lines == ["src/caller.py:45: do_thing"]
    fake_query_graph.assert_called_once_with(
        pattern="callers_of", target="foobar", repo_root="/repo"
    )


def test_query_callers_returns_empty_on_not_found(monkeypatch):
    fake_query_graph = MagicMock(
        return_value={"status": "not_found", "results": [], "edges": []}
    )
    fake_module = ModuleType("code_review_graph")
    fake_tools = ModuleType("code_review_graph.tools")
    fake_query_mod = ModuleType("code_review_graph.tools.query")
    fake_query_mod.query_graph = fake_query_graph
    fake_tools.query = fake_query_mod
    fake_module.tools = fake_tools
    monkeypatch.setitem(sys.modules, "code_review_graph", fake_module)
    monkeypatch.setitem(sys.modules, "code_review_graph.tools", fake_tools)
    monkeypatch.setitem(sys.modules, "code_review_graph.tools.query", fake_query_mod)

    from superseded.context.graph_retrieval import _query_callers

    assert _query_callers("missing", Path("/repo")) == []


def test_query_callers_returns_empty_on_ambiguous(monkeypatch):
    fake_query_graph = MagicMock(
        return_value={
            "status": "ambiguous",
            "candidates": ["a.py::x", "b.py::x"],
            "results": [],
            "edges": [],
        }
    )
    fake_module = ModuleType("code_review_graph")
    fake_tools = ModuleType("code_review_graph.tools")
    fake_query_mod = ModuleType("code_review_graph.tools.query")
    fake_query_mod.query_graph = fake_query_graph
    fake_tools.query = fake_query_mod
    fake_module.tools = fake_tools
    monkeypatch.setitem(sys.modules, "code_review_graph", fake_module)
    monkeypatch.setitem(sys.modules, "code_review_graph.tools", fake_tools)
    monkeypatch.setitem(sys.modules, "code_review_graph.tools.query", fake_query_mod)

    from superseded.context.graph_retrieval import _query_callers

    assert _query_callers("x", Path("/repo")) == []


def test_query_callers_swallows_value_error(monkeypatch, caplog):
    """Bad repo_root raises ValueError inside CRG; we log and return []."""

    def boom(*a, **kw):
        raise ValueError("repo_root does not exist")

    fake_query_graph = boom
    fake_module = ModuleType("code_review_graph")
    fake_tools = ModuleType("code_review_graph.tools")
    fake_query_mod = ModuleType("code_review_graph.tools.query")
    fake_query_mod.query_graph = fake_query_graph
    fake_tools.query = fake_query_mod
    fake_module.tools = fake_tools
    monkeypatch.setitem(sys.modules, "code_review_graph", fake_module)
    monkeypatch.setitem(sys.modules, "code_review_graph.tools", fake_tools)
    monkeypatch.setitem(sys.modules, "code_review_graph.tools.query", fake_query_mod)

    from superseded.context.graph_retrieval import _query_callers

    with caplog.at_level("WARNING"):
        assert _query_callers("anything", Path("/repo")) == []
    assert "raised" in caplog.text


def test_query_callers_swallows_generic_exception(monkeypatch, caplog):
    """sqlite3 corruption or anything else should never abort the review."""

    def boom(*a, **kw):
        raise RuntimeError("db corrupted")

    fake_query_graph = boom
    fake_module = ModuleType("code_review_graph")
    fake_tools = ModuleType("code_review_graph.tools")
    fake_query_mod = ModuleType("code_review_graph.tools.query")
    fake_query_mod.query_graph = fake_query_graph
    fake_tools.query = fake_query_mod
    fake_module.tools = fake_tools
    monkeypatch.setitem(sys.modules, "code_review_graph", fake_module)
    monkeypatch.setitem(sys.modules, "code_review_graph.tools", fake_tools)
    monkeypatch.setitem(sys.modules, "code_review_graph.tools.query", fake_query_mod)

    from superseded.context.graph_retrieval import _query_callers

    with caplog.at_level("WARNING"):
        assert _query_callers("anything", Path("/repo")) == []
    assert "raised" in caplog.text


def test_query_callers_skips_non_calls_edges(monkeypatch):
    """Only CALLS edges count as caller relationships."""
    fake_result = {
        "status": "ok",
        "results": [
            {
                "qualified_name": "src/c.py::caller",
                "name": "caller",
                "file_path": "src/c.py",
                "line_start": 1,
            }
        ],
        "edges": [
            {
                "kind": "TESTED_BY",
                "source": "src/c.py::caller",
                "file_path": "src/c.py",
                "line": 2,
            },
            {
                "kind": "CALLS",
                "source": "src/c.py::caller",
                "file_path": "src/c.py",
                "line": 3,
            },
        ],
    }
    fake_query_graph = MagicMock(return_value=fake_result)
    fake_module = ModuleType("code_review_graph")
    fake_tools = ModuleType("code_review_graph.tools")
    fake_query_mod = ModuleType("code_review_graph.tools.query")
    fake_query_mod.query_graph = fake_query_graph
    fake_tools.query = fake_query_mod
    fake_module.tools = fake_tools
    monkeypatch.setitem(sys.modules, "code_review_graph", fake_module)
    monkeypatch.setitem(sys.modules, "code_review_graph.tools", fake_tools)
    monkeypatch.setitem(sys.modules, "code_review_graph.tools.query", fake_query_mod)

    from superseded.context.graph_retrieval import _query_callers

    lines = _query_callers("sym", Path("/repo"))
    assert lines == ["src/c.py:3: caller"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_graph_retrieval.py::test_query_callers_formats_path_line_name -v`
Expected: FAIL with `AttributeError: module ... has no attribute '_query_callers'`.

- [ ] **Step 3: Implement `_query_callers`**

Append to `src/superseded/context/graph_retrieval.py`:

```python
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
        result = query_graph(
            pattern="callers_of", target=symbol, repo_root=str(root)
        )
    except ValueError as err:
        logger.warning("query_graph callers_of %s raised: %s", symbol, err)
        return []
    except Exception as err:  # noqa: BLE001 - sqlite corruption, etc.
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_graph_retrieval.py -v`
Expected: PASS (13 tests).

- [ ] **Step 5: Commit**

```bash
git add src/superseded/context/graph_retrieval.py tests/test_graph_retrieval.py
git commit -m "feat(graph_retrieval): add _query_callers wrapping CRG query_graph"
```

---

## Task 6: Add `retrieve_usages_via_graph` (top-level entry point)

**Files:**
- Modify: `src/superseded/context/graph_retrieval.py`
- Test: `tests/test_graph_retrieval.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_graph_retrieval.py`:

```python
def _install_fake_query(monkeypatch, per_symbol):
    """Wire up a fake query_graph returning per_symbol[symbol] list of caller
    lines."""
    fake_query_graph = MagicMock(side_effect=lambda pattern, target, repo_root: {
        "status": "ok",
        "results": [],
        "edges": [
            {"kind": "CALLS", "source": f"src/x.py::c{i}", "file_path": "src/x.py", "line": ln}
            for i, ln in enumerate(per_symbol.get(target, []))
        ],
    })
    fake_module = ModuleType("code_review_graph")
    fake_tools = ModuleType("code_review_graph.tools")
    fake_query_mod = ModuleType("code_review_graph.tools.query")
    fake_query_mod.query_graph = fake_query_graph
    fake_tools.query = fake_query_mod
    fake_module.tools = fake_tools
    monkeypatch.setitem(sys.modules, "code_review_graph", fake_module)
    monkeypatch.setitem(sys.modules, "code_review_graph.tools", fake_tools)
    monkeypatch.setitem(sys.modules, "code_review_graph.tools.query", fake_query_mod)


def test_retrieve_usages_via_graph_no_symbols_returns_none():
    from superseded.context.graph_retrieval import retrieve_usages_via_graph

    assert retrieve_usages_via_graph("@@ -1 +1 @@\n unchanged\n", Path("/repo")) is None


def test_retrieve_usages_via_graph_formats_blocks(monkeypatch):
    _install_fake_query(monkeypatch, {"foobar": [10, 20]})
    from superseded.context.graph_retrieval import retrieve_usages_via_graph

    result = retrieve_usages_via_graph(
        "@@ -1 +1 @@\n+def foobar():\n+    pass\n", Path("/repo")
    )
    assert result is not None
    assert "### Usages of `foobar`" in result
    assert "src/x.py:10" in result


def test_retrieve_usages_via_graph_excludes_changed_files(monkeypatch):
    """Callers inside changed files should be filtered out — mirrors rg's
    --glob behavior."""
    # Diff touches src/changed.py; fake query returns a caller from that file
    # and another from src/other.py. Only the other.py caller should appear.
    fake_query_graph = MagicMock(side_effect=lambda pattern, target, repo_root: {
        "status": "ok",
        "results": [],
        "edges": [
            {"kind": "CALLS", "source": "src/changed.py::local", "file_path": "src/changed.py", "line": 5},
            {"kind": "CALLS", "source": "src/other.py::ext", "file_path": "src/other.py", "line": 7},
        ],
    })
    fake_module = ModuleType("code_review_graph")
    fake_tools = ModuleType("code_review_graph.tools")
    fake_query_mod = ModuleType("code_review_graph.tools.query")
    fake_query_mod.query_graph = fake_query_graph
    fake_tools.query = fake_query_mod
    fake_module.tools = fake_tools
    monkeypatch.setitem(sys.modules, "code_review_graph", fake_module)
    monkeypatch.setitem(sys.modules, "code_review_graph.tools", fake_tools)
    monkeypatch.setitem(sys.modules, "code_review_graph.tools.query", fake_query_mod)

    from superseded.context.graph_retrieval import retrieve_usages_via_graph

    diff = "diff --git a/src/changed.py b/src/changed.py\n@@ -1 +1,2 @@\n+def foo():\n+    pass\n"
    result = retrieve_usages_via_graph(diff, Path("/repo"))
    assert result is not None
    assert "src/other.py:7" in result
    assert "src/changed.py:5" not in result


def test_retrieve_usages_via_graph_budget_truncation(monkeypatch):
    """When the running block size exceeds USAGE_BUDGET, the tail-truncation
    marker is added."""
    # Each symbol returns 50 callers (~50 lines * ~25 chars). Many symbols will
    # push us past USAGE_BUDGET (6000).
    per_symbol = {"foobar": list(range(50, 100)), "baz": list(range(50, 100))}
    _install_fake_query(monkeypatch, per_symbol)
    from superseded.context.graph_retrieval import retrieve_usages_via_graph

    diff = "@@ -1 +1,2 @@\n+def foobar():\n+def baz():\n"
    result = retrieve_usages_via_graph(diff, Path("/repo"))
    assert result is not None
    assert "omitted" in result


def test_retrieve_usages_via_graph_returns_none_when_no_callers(monkeypatch):
    """All symbols yield empty caller lists -> None."""
    _install_fake_query(monkeypatch, {})
    from superseded.context.graph_retrieval import retrieve_usages_via_graph

    result = retrieve_usages_via_graph(
        "@@ -1 +1 @@\n+def foo():\n+def bar():\n", Path("/repo")
    )
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_graph_retrieval.py::test_retrieve_usages_via_graph_no_symbols_returns_none -v`
Expected: FAIL with `AttributeError: ... has no attribute 'retrieve_usages_via_graph'`.

- [ ] **Step 3: Implement `retrieve_usages_via_graph`**

Append to `src/superseded/context/graph_retrieval.py`:

```python
def _line_targets_changed_file(line: str, changed: set[str]) -> bool:
    """True if the leading path of a 'path:line: snippet' line is in `changed`."""
    if ":" not in line:
        return False
    path = line.split(":", 1)[0]
    return Path(path).as_posix() in changed


def retrieve_usages_via_graph(
    diff: str, root: Path, *, changed_files: list[str] | None = None
) -> str | None:
    """Graph-grounded drop-in replacement for usage_retrieval.retrieve_usages.

    Reuses extract_symbols() so the symbol set is identical to the rg path.
    Produces `### Usages of \`symbol\`` blocks under the same USAGE_BUDGET.
    Returns None when no symbols or no caller data found.
    """
    entries = parse_diff_files(diff)
    if entries:
        if changed_files is None:
            changed_files = [e["file"] for e in entries]
        symbols: list[str] = []
        seen: set[str] = set()
        for entry in entries:
            lang = _LANG_MAP.get(Path(entry["file"]).suffix)
            for sym in extract_symbols(entry["diff"], lang):
                if sym not in seen:
                    seen.add(sym)
                    symbols.append(sym)
        symbols = symbols[-MAX_SYMBOLS:]
    else:
        changed_files = changed_files or []
        symbols = extract_symbols(diff, None)

    if not symbols:
        return None

    changed_set = {Path(f).as_posix() for f in changed_files}

    blocks: list[str] = []
    total_chars = 0
    for sym in symbols:
        all_lines = _query_callers(sym, root)
        lines = [
            ln for ln in all_lines if not _line_targets_changed_file(ln, changed_set)
        ]
        if not lines:
            continue
        block = f"### Usages of `{sym}`\n" + "\n".join(lines)
        if total_chars + len(block) > USAGE_BUDGET:
            omitted = len(symbols) - len(blocks)
            blocks.append(f"\u2026 ({omitted} more usages omitted by retrieval budget)")
            break
        blocks.append(block)
        total_chars += len(block)

    if not blocks:
        return None
    return "\n\n".join(blocks)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_graph_retrieval.py -v`
Expected: PASS (18 tests).

- [ ] **Step 5: Commit**

```bash
git add src/superseded/context/graph_retrieval.py tests/test_graph_retrieval.py
git commit -m "feat(graph_retrieval): add retrieve_usages_via_graph"
```

---

## Task 7: Wire the graph flag through `gathering.py`

**Files:**
- Modify: `src/superseded/context/gathering.py`
- Test: `tests/test_gathering_graph.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gathering_graph.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


def test_gather_context_graph_false_uses_rg(monkeypatch):
    """When graph=False, the rg path (retrieve_usages) is used."""
    import superseded.context.gathering as g

    rg_called = {"yes": False}
    graph_called = {"yes": False}

    def fake_retrieve_usages(diff, root):
        rg_called["yes"] = True
        return "rg-result"

    def fake_retrieve_via_graph(diff, root, **kwargs):
        graph_called["yes"] = True
        return "graph-result"

    monkeypatch.setattr(g, "retrieve_usages", fake_retrieve_usages)
    monkeypatch.setattr(g.graph_retrieval, "retrieve_usages_via_graph", fake_retrieve_via_graph)
    monkeypatch.setattr(g.graph_retrieval, "is_available", lambda root: True)
    monkeypatch.setattr(g.graph_retrieval, "ensure_graph_fresh", lambda root: None)
    # Other expensive calls get stubbed.
    monkeypatch.setattr(g, "compute_file_context", lambda diff, root=None: "fc")
    monkeypatch.setattr(g, "run_static_analysis", lambda files, root: None)
    monkeypatch.setattr(g, "discover_conventions", lambda root: None)
    monkeypatch.setattr(g, "discover_repo_specs", lambda diff, root: None)

    result = g.gather_context("diff", Path("/repo"), usage_retrieval=True, graph=False)
    assert rg_called["yes"] is True
    assert graph_called["yes"] is False
    assert result["usage_signals"] == "rg-result"


def test_gather_context_graph_true_available_uses_graph(monkeypatch):
    """When graph=True and CRG is available, the graph path is used and the
    graph is refreshed first."""
    import superseded.context.gathering as g

    events: list[str] = []
    refresh_called = {"yes": False}

    def fake_retrieve_usages(diff, root):
        events.append("rg")
        return "rg-result"

    def fake_retrieve_via_graph(diff, root, **kwargs):
        events.append("graph")
        return "graph-result"

    def fake_refresh(root):
        refresh_called["yes"] = True
        events.append("refresh")

    monkeypatch.setattr(g, "retrieve_usages", fake_retrieve_usages)
    monkeypatch.setattr(g.graph_retrieval, "retrieve_usages_via_graph", fake_retrieve_via_graph)
    monkeypatch.setattr(g.graph_retrieval, "is_available", lambda root: True)
    monkeypatch.setattr(g.graph_retrieval, "ensure_graph_fresh", fake_refresh)
    monkeypatch.setattr(g, "compute_file_context", lambda diff, root=None: "fc")
    monkeypatch.setattr(g, "run_static_analysis", lambda files, root: None)
    monkeypatch.setattr(g, "discover_conventions", lambda root: None)
    monkeypatch.setattr(g, "discover_repo_specs", lambda diff, root: None)

    result = g.gather_context("diff", Path("/repo"), usage_retrieval=True, graph=True)
    assert refresh_called["yes"] is True
    # refresh must happen before the graph query (within the same worker thread,
    # so we check the recorded order).
    assert events.index("refresh") < events.index("graph")
    assert result["usage_signals"] == "graph-result"


def test_gather_context_graph_true_unavailable_falls_back(monkeypatch):
    """When graph=True but CRG unavailable, the rg path is used and refresh is
    NOT called."""
    import superseded.context.gathering as g

    refresh_called = {"yes": False}

    monkeypatch.setattr(g, "retrieve_usages", lambda diff, root: "rg-result")
    monkeypatch.setattr(
        g.graph_retrieval, "retrieve_usages_via_graph",
        lambda diff, root, **kw: "graph-result",
    )
    monkeypatch.setattr(g.graph_retrieval, "is_available", lambda root: False)
    monkeypatch.setattr(
        g.graph_retrieval, "ensure_graph_fresh",
        lambda root: refresh_called.__setitem__("yes", True),
    )
    monkeypatch.setattr(g, "compute_file_context", lambda diff, root=None: "fc")
    monkeypatch.setattr(g, "run_static_analysis", lambda files, root: None)
    monkeypatch.setattr(g, "discover_conventions", lambda root: None)
    monkeypatch.setattr(g, "discover_repo_specs", lambda diff, root: None)

    result = g.gather_context("diff", Path("/repo"), usage_retrieval=True, graph=True)
    assert refresh_called["yes"] is False
    assert result["usage_signals"] == "rg-result"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gathering_graph.py -v`
Expected: FAIL — `gather_context()` rejects the unknown `graph=True` keyword (TypeError) or returns the rg result regardless.

- [ ] **Step 3: Implement the `graph` flag**

Edit `src/superseded/context/gathering.py`. Add the `graph_retrieval` import near the existing imports, add the `graph` parameter, and create a `_refresh_then_retrieve` helper that runs sequentially in one worker thread:

```python
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


def _refresh_then_retrieve(
    diff: str, root: Path, changed_files: list[str]
) -> str | None:
    """Refresh the graph then query it. Runs sequentially in one worker thread
    so the refresh completes before any query reads the graph, while other
    context futures continue in parallel."""
    graph_retrieval.ensure_graph_fresh(root)
    return graph_retrieval.retrieve_usages_via_graph(
        diff, root, changed_files=changed_files
    )


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
        [e["file"] for e in parse_diff_files(diff)]
        if (static_analysis or usage_retrieval)
        else []
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gathering_graph.py tests/test_context_usage.py -v`
Expected: PASS for all (the new tests plus the existing usage tests, which don't pass `graph=`).

- [ ] **Step 5: Commit**

```bash
git add src/superseded/context/gathering.py tests/test_gathering_graph.py
git commit -m "feat(gathering): add graph flag routing usage to CRG when available"
```

---

## Task 8: Add `--graph` / `--no-graph` CLI flag + `resolve_graph`

**Files:**
- Modify: `src/superseded/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_resolve_graph_env_overrides_flag(monkeypatch):
    monkeypatch.setenv("SUPERSEDED_GRAPH", "false")
    from superseded.cli import resolve_graph
    from superseded.config import Config

    assert resolve_graph(True, Config()) is False


def test_resolve_graph_env_truthy_overrides_flag(monkeypatch):
    monkeypatch.setenv("SUPERSEDED_GRAPH", "1")
    from superseded.cli import resolve_graph
    from superseded.config import Config

    assert resolve_graph(False, Config()) is True


def test_resolve_graph_flag_overrides_config():
    from superseded.cli import resolve_graph
    from superseded.config import Config

    cfg = Config(graph=False)
    assert resolve_graph(True, cfg) is True
    cfg2 = Config(graph=True)
    assert resolve_graph(False, cfg2) is False


def test_resolve_graph_defaults_to_config():
    from superseded.cli import resolve_graph
    from superseded.config import Config

    assert resolve_graph(None, Config(graph=True)) is True
    assert resolve_graph(None, Config(graph=False)) is False


def test_resolve_graph_defaults_true():
    from superseded.cli import resolve_graph
    from superseded.config import Config

    assert resolve_graph(None, Config()) is True


def test_review_passes_graph_to_gather_context(monkeypatch):
    """`--graph`/`--no-graph` must propagate as the `graph` kwarg to
    `gather_context`."""
    from click.testing import CliRunner

    from superseded import cli as cli_mod
    from superseded.cli import cli

    captured = {}

    def fake_gather_context(diff, root, **kwargs):
        captured.update(kwargs)
        return {
            "file_context": None,
            "static_signals": None,
            "usage_signals": None,
            "conventions_signals": None,
            "spec_signals": None,
        }

    def fake_fetch_diff(*, pr, diff_range, files):
        return "diff"

    def fake_engine_review(*a, **kw):
        from superseded.models import ReviewResult
        return ReviewResult(findings=[], warnings=[])

    monkeypatch.setattr(cli_mod, "gather_context", fake_gather_context)
    monkeypatch.setattr(cli_mod, "fetch_diff", fake_fetch_diff)
    monkeypatch.setattr(cli_mod, "repo_root", lambda: Path("/repo"))
    monkeypatch.setattr(cli_mod, "fetch_pr_description", lambda pr: None)
    # Stub ReviewEngine.select + agent availability so we don't need an agent.
    fake_engine = MagicMock()
    fake_engine.agent.is_available.return_value = True
    fake_engine.review = fake_engine_review
    monkeypatch.setattr(cli_mod.ReviewEngine, "select", classmethod(lambda cls, *a, **kw: fake_engine))

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["review", "--diff", "HEAD~1..HEAD", "--no-graph", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    assert captured.get("graph") is False

    captured.clear()
    result = runner.invoke(
        cli,
        ["review", "--diff", "HEAD~1..HEAD", "--graph", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    assert captured.get("graph") is True
```

If `from pathlib import Path` and `from unittest.mock import MagicMock` are not already imported at the top of `tests/test_cli.py`, add them.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py::test_resolve_graph_defaults_true -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_graph'`.

- [ ] **Step 3: Implement the flag and resolver**

Edit `src/superseded/cli.py`:

1. Add the env var constant near `AGENT_ENV`/`MODEL_ENV` (line 36-37):

```python
AGENT_ENV = "SUPERSEDED_AGENT"
MODEL_ENV = "SUPERSEDED_MODEL"
GRAPH_ENV = "SUPERSEDED_GRAPH"
DEFAULT_TIMEOUT = 600
```

2. Add `resolve_graph` after `resolve_model` (line 62-63):

```python
def resolve_graph(cli_value: bool | None, config: Config) -> bool:
    env = os.environ.get(GRAPH_ENV)
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    if cli_value is not None:
        return cli_value
    return config.graph
```

3. Add the `--graph/--no-graph` click option on the `review` command (place it next to the existing `--no-specs` option, around line 174). Also add the `graph` parameter to the `review` function signature and pass it into `_run_review`:

```python
@click.option("--no-specs", is_flag=True, help="Disable design spec/plan retrieval")
@click.option(
    "--graph/--no-graph",
    "graph",
    default=None,
    help="Toggle graph-grounded usage retrieval (default: from config)",
)
@click.argument("files", nargs=-1, type=click.Path(exists=True, dir_okay=False))
def review(
    pr: int | None,
    diff_range: str | None,
    agent: str | None,
    model: str | None,
    output_format: str | None,
    post: bool,
    passes: str | None,
    timeout: int | None,
    config_path: Path | None,
    no_memory: bool,
    no_static: bool,
    no_usage: bool,
    no_conventions: bool,
    no_specs: bool,
    graph: bool | None,
    files: tuple[str, ...],
) -> None:
```

And at the bottom of the `review` function's `_run_review` call, add `graph=graph`:

```python
    _run_review(
        pr=pr,
        diff_range=diff_range,
        agent=agent,
        model=model,
        output_format=output_format,
        post=post,
        passes=pass_list,
        timeout=timeout,
        config_path=config_path,
        no_memory=no_memory,
        no_static=no_static,
        no_usage=no_usage,
        no_conventions=no_conventions,
        no_specs=no_specs,
        graph=graph,
        files=list(files) or None,
    )
```

4. Add `graph: bool | None = None` to `_run_review`'s signature (after `no_specs`, before `files`), and add the resolution + gather_context wiring. Inside `_run_review`, after `enable_specs = config.spec_retrieval and not no_specs`:

```python
    enable_graph = resolve_graph(graph, config)
```

And modify the `gather_context` call to pass `graph=enable_graph`:

```python
    context = gather_context(
        diff,
        root,
        static_analysis=enable_static,
        usage_retrieval=enable_usage,
        conventions=enable_conventions,
        spec_retrieval=enable_specs,
        graph=enable_graph,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS including the 6 new tests.

- [ ] **Step 5: Commit**

```bash
git add src/superseded/cli.py tests/test_cli.py
git commit -m "feat(cli): add --graph/--no-graph flag and SUPERSEDED_GRAPH env var"
```

---

## Task 9: Surface CRG status in `superseded init`

**Files:**
- Modify: `src/superseded/detection.py` (already done in Task 2)
- Modify: `src/superseded/cli.py` (`_run_init` only)
- Test: `tests/test_init.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_init.py`:

```python
def test_init_crg_missing_prints_instruction(tmp_path, monkeypatch):
    """When CRG is not detected, init prints the install-instruction line to
    stderr and still succeeds."""
    _patch_detection(
        monkeypatch,
        agents=[AgentStatus("opencode", True, "opencode")],
        gh=True,
    )
    monkeypatch.setattr("superseded.cli.detect_code_review_graph", lambda root: False)
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 0, result.output
    assert target.exists()
    assert "code-review-graph" in result.output
    assert "uv add code-review-graph" in result.output


def test_init_crg_present_prints_found(tmp_path, monkeypatch):
    """When CRG is detected, init prints 'code-review-graph: found'."""
    _patch_detection(
        monkeypatch,
        agents=[AgentStatus("opencode", True, "opencode")],
        gh=True,
    )
    monkeypatch.setattr("superseded.cli.detect_code_review_graph", lambda root: True)
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 0, result.output
    assert "code-review-graph: found" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_init.py::test_init_crg_missing_prints_instruction -v`
Expected: FAIL — the `code-review-graph` line isn't printed yet.

- [ ] **Step 3: Wire the probe into `_run_init`**

Edit `src/superseded/cli.py`:

1. Add `detect_code_review_graph` to the import block from `superseded.detection` (around line 16-21):

```python
from superseded.detection import (
    default_model_for,
    detect_agents,
    detect_code_review_graph,
    detect_gh,
    pick_agent,
)
```

2. Inside `_run_init`, after the `gh_ok` block and before the `agent_override` block, add:

```python
    crg_root = Path.cwd()
    if detect_code_review_graph(crg_root):
        _status("code-review-graph: found")
    else:
        _status(
            "code-review-graph: not installed "
            "(graph-grounded reviews disabled; install with: "
            "uv add code-review-graph && code-review-graph build)"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_init.py -v`
Expected: PASS including the 2 new tests.

- [ ] **Step 5: Commit**

```bash
git add src/superseded/cli.py tests/test_init.py
git commit -m "feat(init): report code-review-graph availability"
```

---

## Task 10: Add optional `graph` dependency group

**Files:**
- Modify: `pyproject.toml`

This task has no automated test — it's a packaging change. We verify by re-locking.

- [ ] **Step 1: Add the optional-dependency group**

Edit `pyproject.toml`, inserting the `[project.optional-dependencies]` table after the `[dependency-groups]` block (after line 22):

```toml
[project.optional-dependencies]
graph = ["code-review-graph"]
```

- [ ] **Step 2: Re-lock and verify**

Run: `uv lock`
Expected: success (uv updates `uv.lock` to record the optional group without installing it).

Run: `uv sync --extra graph`
Expected: success. `code-review-graph` and its deps land in `.venv/`.

Run: `uv run python -c "from code_review_graph.tools.query import query_graph; print(query_graph.__doc__[:80])"`
Expected: prints the start of the `query_graph` docstring (verification that the import path used in Task 5 works against the real package).

If the install or import fails, do NOT proceed. Investigate the upstream package or pin a version in `pyproject.toml` (e.g. `"code-review-graph>=2.3"`).

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build(deps): add optional 'graph' extra (code-review-graph)"
```

---

## Task 11: Update `AGENTS.md` docs

**Files:**
- Modify: `AGENTS.md`

No test — docs.

- [ ] **Step 1: Document the new behavior**

Edit `AGENTS.md`. In the **Conventions** section, extend the paragraph to mention the new toggle. Find the existing sentence ending with `... or \`--no-conventions\` / \`--no-specs\`.` and extend it:

```markdown
- `Config.conventions` and `Config.spec_retrieval` (default `true`) inject repo-grounded convention docs and diff-relevant specs/plans/skills into every pass prompt. Disable with `.superseded.yaml` `conventions: false` / `spec_retrieval: false`, or `--no-conventions` / `--no-specs`. See `context/conventions.py` and `context/spec_retrieval.py`.
- `Config.graph` (default `true`) routes usage retrieval through `code-review-graph` when installed and a built graph exists at `.code-review-graph/`; otherwise the rg path in `context/usage_retrieval.py` runs unchanged. Toggle precedence mirrors agent/model: `SUPERSEDED_GRAPH` env > `--graph`/`--no-graph` flag > config file. Install the optional dep with `uv sync --extra graph` (or `uv add code-review-graph`) then `code-review-graph build`.
```

In **Architecture notes**, add one bullet after the existing bullets:

```markdown
- Usage retrieval has two interchangeable paths: the default rg-based `context/usage_retrieval.py` (calls `rg` over the repo for changed symbols) and the graph-grounded `context/graph_retrieval.py` (queries `code-review-graph`'s in-process `query_graph` for callers of those symbols). `context/gathering.py` picks at runtime based on `is_available(root)` and the resolved `graph` toggle. Refresh-before-query (`code-review-graph update --brief`) runs in the same worker thread as the query so it always completes first; both run in parallel with the other context futures.
```

In **Gitignore gotchas**, append one line:

```markdown
- `.code-review-graph/` (CRG's local SQLite graph DB and artifacts) is gitignored. Don't commit it.
```

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs(agents): document graph integration"
```

---

## Task 12: Full-suite verification

**Files:** None (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: ALL tests pass. No skips related to CRG (the graph path is exercised via mocks; CRG being installed is incidental).

- [ ] **Step 2: Run lint + format checks**

Run: `uv run ruff check src/ tests/`
Expected: clean.

Run: `uv run ruff format --check src/ tests/`
Expected: clean. If not, run `uv run ruff format src/ tests/` and re-stage any changes.

- [ ] **Step 3: Smoke-test the CLI locally**

Run: `uv run superseded review --help | rg graph`
Expected: the `--graph/--no-graph` line appears in the help text.

Run: `uv run superseded init --help`
Expected: the existing init help is unchanged (no new init flags).

Run: `uv run superseded review --diff HEAD~1..HEAD --no-graph --format json | head -5`
Expected: review runs (or fails on missing agent — that's fine, the point is it accepted `--no-graph`).

- [ ] **Step 4: Commit any format/lint fixes**

```bash
git status
# If anything changed:
git add -A
git commit -m "chore: ruff format/lint fixes from final verification"
```

---

## Self-review checklist

After completing all tasks, run through this checklist before declaring done:

1. **Spec coverage:**
   - [x] `graph: bool` in Config — Task 1
   - [x] `detect_code_review_graph` — Task 2
   - [x] `is_available` / `ensure_graph_fresh` / `retrieve_usages_via_graph` — Tasks 3-6
   - [x] `gather_context(graph=...)` wiring — Task 7
   - [x] `--graph`/`--no-graph` + `SUPERSEDED_GRAPH` + `resolve_graph` — Task 8
   - [x] `init` CRG status line — Task 9
   - [x] `[project.optional-dependencies] graph` — Task 10
   - [x] Docs updates — Task 11

2. **Error model** (every CRG touchpoint swallows failures and falls back to rg):
   - [x] Import missing → `is_available` False → rg path
   - [x] Graph dir missing → `is_available` False → rg path
   - [x] Refresh `FileNotFoundError` / `TimeoutExpired` / `OSError` → logged, query continues against stale graph
   - [x] Query `ValueError` (bad repo_root) → logged, empty list for that symbol
   - [x] Query generic exception → logged, empty list for that symbol
   - [x] All symbols empty → `retrieve_usages_via_graph` returns None → no usage_signals block

3. **Type/name consistency:**
   - `is_available(root: Path) -> bool` — used identically in Tasks 3, 7
   - `ensure_graph_fresh(root: Path) -> None` — used identically in Tasks 4, 7
   - `retrieve_usages_via_graph(diff, root, *, changed_files=None) -> str | None` — used in Tasks 6, 7
   - `resolve_graph(cli_value: bool | None, config: Config) -> bool` — Task 8 defines and uses
   - `_refresh_then_retrieve(diff, root, changed_files)` — Task 7 internal helper
   - `graph_retrieval` imported as a module alias in `gathering.py` (so tests can monkeypatch attributes without re-imports)

4. **No placeholders:** Every step shows concrete code or commands.
