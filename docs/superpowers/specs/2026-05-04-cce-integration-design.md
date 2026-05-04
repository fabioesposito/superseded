# CCE Integration Design: Context Engineering for Superseded

**Date:** 2026-05-04
**Status:** Approved for implementation

## Problem

Superseded's ContextAssembler builds prompts with a docs index layer that reads every `docs/**/*.md` file and a session history layer that replays past turns (truncated at 2000 chars). Agents get full context upfront with no targeted search. This wastes tokens and causes context rot on long-running tasks.

## Solution

Integrate [Code Context Engine (CCE)](https://github.com/elara-labs/code-context-engine) as the context engineering layer. CCE provides:
- AST-indexed codebase search (tree-sitter)
- Hybrid vector + BM25 retrieval (94% token savings)
- Cross-session memory (`record_decision` / `session_recall`)
- Chunk compression (signatures + docstrings)

## Architecture

### Integration Point

CCE runs as a CLI tool (`cce`). The harness calls it via subprocess, same pattern as agent adapters. No Python library dependency — just the `cce` binary in `$PATH`.

### Flow

```
1. Harness.run_stage() called
2. Harness._ensure_indexed(repo_path) → runs `cce index` if not indexed
3. ContextAssembler.build() called with CCE-enhanced layers
4. Agent receives prompt with CCE MCP tool descriptions
5. Agent calls context_search, record_decision, session_recall as needed
6. On stage completion, harness calls record_decision with stage outcome
```

### ContextAssembler Changes

Replace two layers:

| Layer | Before | After |
|---|---|---|
| Docs index | Reads all `docs/**/*.md` | CCE `context_search("project architecture")` results |
| Session history | Replays past turns from DB | CCE `session_recall()` results |

Add one layer:

| New Layer | Content |
|---|---|
| CCE tools | MCP tool descriptions injected so agents know they can search |

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

`Harness._ensure_indexed(repo_path)` checks if CCE index exists:
- If not: runs `cce index --quiet` (creates `.context-engine/` in repo)
- If yes: skips (index persists across runs)
- Re-index: runs `cce reindex --quiet` if index is stale (> 1 hour old)

### Cross-Session Memory

After each stage completes, the harness calls:
```
cce record-decision "Stage {stage} completed for {issue_id}" --reason "{summary}"
```

Before each stage, the ContextAssembler calls:
```
cce session-recall
```
and injects results as the session history layer.

### CCE Tools Layer

New layer in ContextAssembler that injects:

```
## Code Context Tools

You have access to the following code search tools via the CCE MCP server:

- `context_search(query)` — Search the codebase for relevant code chunks. Use this instead of reading entire files.
- `expand_chunk(chunk_id)` — Get full source for a compressed result.
- `related_context(file)` — Find code via graph edges (calls, imports).
- `session_recall()` — Recall decisions from past sessions.
- `record_decision(decision, reason)` — Save a decision for future sessions.
- `record_code_area(file)` — Record which files you're working in.

Use `context_search` to find relevant code before reading files. This saves tokens and finds the right code faster.
```

### Config

Add to `SupersededConfig`:

```python
class CCEConfig(BaseModel):
    enabled: bool = False
    auto_index: bool = True
    index_stale_minutes: int = 60
    compression_level: str = "standard"  # off, lite, standard, max
```

Add to `SupersededConfig`:
```python
cce: CCEConfig = Field(default_factory=CCEConfig)
```

Config example:
```yaml
cce:
  enabled: true
  auto_index: true
  index_stale_minutes: 60
  compression_level: standard
```

### Agent Detection

On startup, check if `cce` is available:
```python
import shutil
cce_available = shutil.which("cce") is not None
```

If `cce.enabled: true` but `cce` not found, log a warning and fall back to the old context assembly.

## Implementation

### Files to modify

| File | Change |
|---|---|
| `src/superseded/config.py` | Add `CCEConfig` model |
| `src/superseded/harness/cce.py` | New — CCE wrapper (index, search, record, recall) |
| `src/superseded/harness/context.py` | Replace docs index + session history layers with CCE |
| `src/superseded/harness/__init__.py` | Call `_ensure_indexed()` before stages, record decisions after |
| `templates/settings.html` | Add CCE config section |
| `templates/_cce_field.html` | CCE settings partial |
| `tests/test_cce.py` | Tests for CCE wrapper |
| `tests/test_context.py` | Update for CCE-enhanced context assembly |

### New file: `src/superseded/harness/cce.py`

```python
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CCESearchResult:
    file: str
    chunk: str
    score: float
    compressed: str


class CCEClient:
    def __init__(self, repo_path: str) -> None:
        self.repo_path = Path(repo_path)
        self._available = shutil.which("cce") is not None

    @property
    def available(self) -> bool:
        return self._available

    async def _run(self, *args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "cce", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.repo_path),
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning("cce %s failed: %s", args[0], stderr.decode())
            return ""
        return stdout.decode()

    async def index(self) -> bool:
        result = await self._run("index", "--quiet")
        return bool(result)

    async def reindex(self) -> bool:
        result = await self._run("reindex", "--quiet")
        return bool(result)

    async def search(self, query: str, top_k: int = 10) -> list[CCESearchResult]:
        result = await self._run("search", query, "--top-k", str(top_k), "--json")
        if not result:
            return []
        try:
            data = json.loads(result)
            return [
                CCESearchResult(
                    file=item.get("file", ""),
                    chunk=item.get("chunk", ""),
                    score=item.get("score", 0.0),
                    compressed=item.get("compressed", ""),
                )
                for item in data
            ]
        except json.JSONDecodeError:
            return []

    async def record_decision(self, decision: str, reason: str = "") -> None:
        args = ["record-decision", decision]
        if reason:
            args.extend(["--reason", reason])
        await self._run(*args)

    async def session_recall(self) -> str:
        return await self._run("session-recall")

    async def is_indexed(self) -> bool:
        index_dir = self.repo_path / ".context-engine"
        return index_dir.exists()

    async def is_stale(self, max_age_minutes: int = 60) -> bool:
        index_dir = self.repo_path / ".context-engine"
        if not index_dir.exists():
            return True
        db_file = index_dir / "index.db"
        if not db_file.exists():
            return True
        import time
        age = time.time() - db_file.stat().st_mtime
        return age > max_age_minutes * 60
```

## Success Criteria

1. `cce.enabled: true` in config → agents get CCE tools and search results
2. `cce.enabled: false` (default) → old behavior preserved
3. Auto-indexing creates `.context-engine/` on first run
4. `context_search` results replace docs index layer
5. `session_recall` results replace session history layer
6. `record_decision` called after each stage completion
7. Fallback to old behavior if `cce` not installed
8. Settings UI shows CCE config section
