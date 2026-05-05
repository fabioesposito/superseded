# CRG Integration Design: Context Engineering for Superseded

**Date:** 2026-05-04
**Status:** Approved for implementation

## Problem

Superseded's ContextAssembler builds prompts with a docs index layer that reads every `docs/**/*.md` file and a session history layer that replays past turns (truncated at 2000 chars). Agents get full context upfront with no targeted search. This wastes tokens and causes context rot on long-running tasks.

## Solution

Integrate [Code Review Graph (CRG)](https://github.com/tirth8205/code-review-graph) as the context engineering layer. CRG provides:
- AST-indexed codebase search (tree-sitter, 23 languages)
- Blast-radius analysis (impact of changes across dependency graph)
- Incremental graph updates (< 2 seconds on file changes)
- 8.2x average token reduction across real repositories
- 28 MCP tools for code search, traversal, and review

## Architecture

### Integration Point

CRG runs as a CLI tool (`code-review-graph`). The harness calls it via subprocess, same pattern as agent adapters. No Python library dependency — just the `code-review-graph` binary in `$PATH`.

### Flow

```
1. Harness.run_stage() called
2. Harness._ensure_graph_built(repo_path) → runs `code-review-graph build` if not built
3. ContextAssembler.build() called with CRG-enhanced layers
4. Agent receives prompt with CRG MCP tool descriptions
5. Agent calls query_graph, semantic_search_nodes, get_impact_radius as needed
6. On stage completion, harness records stage outcome in session
```

### ContextAssembler Changes

Replace two layers:

| Layer | Before | After |
|---|---|---|
| Docs index | Reads all `docs/**/*.md` | CRG `semantic_search_nodes` + `get_review_context` results |
| Session history | Replays past turns from DB | CRG `get_minimal_context` structural summary |

Add one layer:

| New Layer | Content |
|---|---|
| CRG tools | MCP tool descriptions injected so agents know they can search |

Keep unchanged:
- AGENTS.md layer
- Issue ticket layer
- Target repo context layer
- Artifacts layer
- Answers layer
- Rules layer
- Skill prompt layer
- Error context layer

### Auto-Indexing

`Harness._ensure_graph_built(repo_path)` checks if CRG graph exists:
- If not: runs `code-review-graph build` (creates `.code-review-graph/` in repo)
- If yes: skips (graph persists across runs)
- Re-index: runs `code-review-graph update` if graph is stale (> 1 hour old)

### Cross-Session Memory

After each stage completes, the harness records the stage outcome in the session context for the next stage.

### CRG Tools Layer

New layer in ContextAssembler that injects:

```
## Code Review Graph Tools

You have access to the following code analysis tools via the CRG MCP server:

- `get_minimal_context_tool(query)` — Ultra-compact context (~100 tokens). Call this first.
- `semantic_search_nodes_tool(query)` — Search code entities by name or meaning.
- `query_graph_tool(node, query_type)` — Query callers, callees, tests, imports, inheritance.
- `get_impact_radius_tool(files)` — Blast radius of changed files.
- `get_review_context_tool()` — Token-optimised review context with structural summary.
- `traverse_graph_tool(node, depth, token_budget)` — BFS/DFS traversal from any node.
- `detect_changes_tool()` — Risk-scored change impact analysis.
- `list_communities_tool()` — List detected code communities.
- `get_architecture_overview_tool()` — Architecture overview from community structure.

Use `get_minimal_context_tool` or `semantic_search_nodes_tool` to find relevant code before reading entire files. This saves tokens and finds the right code faster.
```

### Config

Add to `SupersededConfig`:

```python
class CRGConfig(BaseModel):
    enabled: bool = False
    auto_build: bool = True
    graph_stale_minutes: int = 60
```

Add to `SupersededConfig`:
```python
crg: CRGConfig = Field(default_factory=CRGConfig)
```

Config example:
```yaml
crg:
  enabled: true
  auto_build: true
  graph_stale_minutes: 60
```

### Agent Detection

On startup, check if `code-review-graph` is available:
```python
import shutil
crg_available = shutil.which("code-review-graph") is not None
```

If `crg.enabled: true` but `code-review-graph` not found, log a warning and fall back to the old context assembly.

## Implementation

### Files to modify

| File | Change |
|---|---|
| `src/superseded/config.py` | Add `CRGConfig` model |
| `src/superseded/harness/crg.py` | New — CRG wrapper (build, update, search) |
| `src/superseded/harness/context.py` | Replace docs index + session history layers with CRG |
| `src/superseded/harness/__init__.py` | Call `_ensure_graph_built()` before stages |
| `templates/settings.html` | Add CRG config section |
| `templates/_crg_field.html` | CRG settings partial |
| `tests/test_crg.py` | Tests for CRG wrapper |
| `tests/test_context.py` | Update for CRG-enhanced context assembly |

### New file: `src/superseded/harness/crg.py`

```python
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CRGSearchResult:
    file: str
    node: str
    score: float
    context: str


class CRGClient:
    def __init__(self, repo_path: str, crg_bin: str = "code-review-graph") -> None:
        self.repo_path = Path(repo_path)
        self.crg_bin = crg_bin
        self._available = shutil.which(crg_bin) is not None

    @property
    def available(self) -> bool:
        return self._available

    async def _run(self, *args: str, timeout: float = 60.0) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.crg_bin, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.repo_path),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            if proc.returncode != 0:
                logger.warning("crg %s failed: %s", args[0], stderr.decode()[:200])
                return ""
            return stdout.decode()
        except TimeoutError:
            logger.warning("crg %s timed out after %ds", args[0], int(timeout))
            return ""
        except FileNotFoundError:
            logger.warning("crg binary not found: %s", self.crg_bin)
            return ""

    async def build(self) -> bool:
        result = await self._run("build", timeout=120.0)
        return bool(result)

    async def update(self) -> bool:
        result = await self._run("update", timeout=120.0)
        return bool(result)

    async def status(self) -> str:
        return await self._run("status", timeout=30.0)

    async def detect_changes(self) -> str:
        return await self._run("detect-changes", timeout=30.0)

    def is_built(self) -> bool:
        graph_dir = self.repo_path / ".code-review-graph"
        return graph_dir.exists()

    def is_stale(self, max_age_minutes: int = 60) -> bool:
        graph_dir = self.repo_path / ".code-review-graph"
        if not graph_dir.exists():
            return True
        db_file = graph_dir / "graph.db"
        if not db_file.exists():
            return True
        return (time.time() - db_file.stat().st_mtime) > max_age_minutes * 60
```

## Success Criteria

1. `crg.enabled: true` in config → agents get CRG tools and search results
2. `crg.enabled: false` (default) → old behavior preserved
3. Auto-building creates `.code-review-graph/` on first run
4. `semantic_search` results replace docs index layer
5. `get_minimal_context` replaces session history layer
6. Fallback to old behavior if `code-review-graph` not installed
7. Settings UI shows CRG config section
