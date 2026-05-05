# CRG Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate Code Review Graph (CRG) as the context engineering layer — auto-build graphs, replace docs index with CRG search, replace session history with minimal context, inject CRG tool descriptions into agent prompts.

**Architecture:** CRG runs as a CLI subprocess (`code-review-graph build`, `code-review-graph update`, `code-review-graph status`). A `CRGClient` wrapper handles all CRG interactions. The `ContextAssembler` gets new layers for CRG search results and tool descriptions. The `Harness` auto-builds graphs before stages.

**Tech Stack:** Python 3.14, asyncio subprocess, CRG CLI (`code-review-graph`)

---

## File Structure

| File | Action | Purpose |
|------|--------|---------|
| `src/superseded/config.py` | Modify | Add `CRGConfig` model |
| `src/superseded/harness/crg.py` | Create | CRGClient wrapper (build, update, detect-changes) |
| `src/superseded/harness/context.py` | Modify | Replace docs index + session history layers with CRG |
| `src/superseded/harness/__init__.py` | Modify | Auto-build before stages |
| `templates/settings.html` | Modify | Add CRG config section |
| `templates/_crg_field.html` | Create | CRG settings partial |
| `tests/test_crg.py` | Create | Tests for CRGClient |
| `tests/test_context.py` | Modify | Update for CRG-enhanced context assembly |

---

### Task 1: Add CRGConfig to config.py

**Files:**
- Modify: `src/superseded/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_crg_config_defaults():
    from superseded.config import CRGConfig
    cfg = CRGConfig()
    assert cfg.enabled is False
    assert cfg.auto_build is True
    assert cfg.graph_stale_minutes == 60


def test_superseded_config_with_crg():
    from superseded.config import CRGConfig
    cfg = SupersededConfig(crg=CRGConfig(enabled=True))
    assert cfg.crg.enabled is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_config.py -v -k "crg"`
Expected: FAIL — `ImportError: cannot import name 'CRGConfig'`

- [ ] **Step 3: Add CRGConfig to config.py**

Add after `ResourceLimitsConfig`:

```python
class CRGConfig(BaseModel):
    enabled: bool = False
    auto_build: bool = True
    graph_stale_minutes: int = 60
```

Add `crg` field to `SupersededConfig`:

```python
crg: CRGConfig = Field(default_factory=CRGConfig)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_config.py -v -k "crg"`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/config.py tests/test_config.py
git commit -m "feat: add CRGConfig for Code Review Graph integration"
```

---

### Task 2: Create CRGClient wrapper

**Files:**
- Create: `src/superseded/harness/crg.py`
- Create: `tests/test_crg.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_crg.py
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from superseded.harness.crg import CRGClient


class TestCRGClient:
    def test_available_when_crg_in_path(self):
        with patch("shutil.which", return_value="/usr/bin/code-review-graph"):
            client = CRGClient("/tmp/test")
            assert client.available is True

    def test_not_available_when_crg_missing(self):
        with patch("shutil.which", return_value=None):
            client = CRGClient("/tmp/test")
            assert client.available is False

    def test_is_built_false_when_no_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = CRGClient(tmp)
            assert client.is_built() is False

    def test_is_built_true_when_dir_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".code-review-graph").mkdir()
            client = CRGClient(tmp)
            assert client.is_built() is True

    def test_is_stale_when_no_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = CRGClient(tmp)
            assert client.is_stale() is True

    def test_is_stale_false_when_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx = Path(tmp) / ".code-review-graph"
            idx.mkdir()
            db = idx / "graph.db"
            db.write_text("test")
            client = CRGClient(tmp)
            assert client.is_stale(max_age_minutes=60) is False

    def test_is_stale_true_when_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx = Path(tmp) / ".code-review-graph"
            idx.mkdir()
            db = idx / "graph.db"
            db.write_text("test")
            old_time = __import__("time").time() - 7200
            os.utime(db, (old_time, old_time))
            client = CRGClient(tmp)
            assert client.is_stale(max_age_minutes=60) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_crg.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'superseded.harness.crg'`

- [ ] **Step 3: Create `src/superseded/harness/crg.py`**

```python
from __future__ import annotations

import asyncio
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_crg.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/harness/crg.py tests/test_crg.py
git commit -m "feat: add CRGClient wrapper for Code Review Graph"
```

---

### Task 3: Integrate CRG into ContextAssembler

**Files:**
- Modify: `src/superseded/harness/context.py`

- [ ] **Step 1: Read current ContextAssembler**

Read `src/superseded/harness/context.py` to understand the layer structure.

- [ ] **Step 2: Add CRG search results layer**

Add a new method to `ContextAssembler`:

```python
def _build_crg_search_layer(self, query: str, results: list) -> str | None:
    if not results:
        return None
    parts = [f"## Code Search Results: \"{query}\"\n"]
    for r in results:
        parts.append(f"### {r.file} (score: {r.score:.2f})\n```\n{r.context}\n```")
    return "\n\n".join(parts)

def _build_crg_tools_layer(self) -> str:
    return (
        "## Code Review Graph Tools\n\n"
        "You have access to code analysis via the CRG MCP server:\n\n"
        "- `get_minimal_context_tool(query)` — Ultra-compact context (~100 tokens). "
        "Call this first.\n"
        "- `semantic_search_nodes_tool(query)` — Search code entities by name or meaning.\n"
        "- `query_graph_tool(node, query_type)` — Query callers, callees, tests, imports, "
        "inheritance.\n"
        "- `get_impact_radius_tool(files)` — Blast radius of changed files.\n"
        "- `get_review_context_tool()` — Token-optimised review context with structural summary.\n"
        "- `traverse_graph_tool(node, depth, token_budget)` — BFS/DFS traversal from any node.\n"
        "- `detect_changes_tool()` — Risk-scored change impact analysis.\n"
        "- `list_communities_tool()` — List detected code communities.\n"
        "- `get_architecture_overview_tool()` — Architecture overview from community structure.\n\n"
        "Use `get_minimal_context_tool` or `semantic_search_nodes_tool` to find relevant code "
        "before reading entire files. This saves tokens and finds the right code faster."
    )
```

- [ ] **Step 3: Modify `build()` to accept CRG results**

Update the `build()` method signature to accept optional CRG results:

```python
def build(
    self,
    stage: Stage,
    issue: Issue,
    artifacts_path: str,
    previous_errors: list[str] | None = None,
    iteration: int = 0,
    session_turns: list[dict] | None = None,
    target_repo: str | None = None,
    crg_search_results: list | None = None,
    crg_enabled: bool = False,
) -> str:
```

In the build method, replace the docs index and session history layers:

```python
# Replace docs index with CRG search results if available
if crg_enabled and crg_search_results:
    crg_layer = self._build_crg_search_layer("codebase", crg_search_results)
    if crg_layer:
        layers.append(crg_layer)
else:
    docs_index = self._build_docs_index_layer()
    if docs_index:
        layers.append(docs_index)

# Replace session history with CRG tools if available
if crg_enabled:
    crg_tools = self._build_crg_tools_layer()
    layers.append(crg_tools)
else:
    session_history = self._build_session_history_layer(stage, session_turns)
    if session_history:
        layers.append(session_history)
```

- [ ] **Step 4: Run existing context tests**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_context.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/harness/context.py
git commit -m "feat: integrate CRG search results and tools into context assembly"
```

---

### Task 4: Wire CRG into Harness

**Files:**
- Modify: `src/superseded/harness/__init__.py`

- [ ] **Step 1: Add CRG client to Harness**

In `Harness.__init__`, add:

```python
from superseded.harness.crg import CRGClient

self.crg_client = CRGClient(repo_path)
```

- [ ] **Step 2: Add auto-build method**

```python
async def _ensure_crg_built(self) -> None:
    if not self.crg_client.available:
        return
    if not self.crg_client.is_built():
        logger.info("CRG graph not found, building %s", self.repo_path)
        await self.crg_client.build()
    elif self.crg_client.is_stale():
        logger.info("CRG graph stale, updating %s", self.repo_path)
        await self.crg_client.update()
```

- [ ] **Step 3: Wire into run_stage**

In `Harness._run_stage_streaming()`, before building context:

```python
# Auto-build if CRG is available
if self.crg_client.available:
    await self._ensure_crg_built()
```

- [ ] **Step 4: Pass CRG results to context assembler**

In `_run_stage_streaming`, get CRG status and pass to context assembler:

```python
crg_enabled = self.crg_client.available
if crg_enabled:
    await self._ensure_crg_built()

prompt = self.context_assembler.build(
    stage=stage, issue=issue, artifacts_path=artifacts_path,
    previous_errors=previous_errors, iteration=0, target_repo=repo,
    crg_enabled=crg_enabled,
)
```

- [ ] **Step 5: Run full test suite**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/ -v -k "not test_run_streaming_yields_stderr" --tb=short`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/superseded/harness/__init__.py
git commit -m "feat: wire CRG auto-build into harness pipeline"
```

---

### Task 5: Add CRG config to Settings UI

**Files:**
- Create: `templates/_crg_field.html`
- Modify: `templates/settings.html`
- Modify: `src/superseded/routes/web/settings.py`

- [ ] **Step 1: Create CRG settings partial**

```html
<div id="crg-config">
    {% if success %}
    <div class="mb-4 px-5 py-3 text-sm text-olive-400 bg-olive-900/20 rounded-lg border border-olive-800/30">
        CRG settings saved successfully.
    </div>
    {% endif %}
    <div class="card rounded-xl p-6">
        <form hx-post="/settings/crg" hx-target="#crg-config" hx-swap="outerHTML">
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                <div>
                    <label class="flex items-center gap-2 cursor-pointer">
                        <input type="checkbox" name="enabled" {% if crg.enabled %}checked{% endif %}
                               class="rounded border-shell-700 bg-shell-900 text-neon-500 focus:ring-neon-500">
                        <span class="text-sm text-shell-200">Enable CRG</span>
                    </label>
                    <p class="text-shell-500 text-xs mt-1">Code Review Graph for token-efficient code search</p>
                </div>
                <div>
                    <label class="flex items-center gap-2 cursor-pointer">
                        <input type="checkbox" name="auto_build" {% if crg.auto_build %}checked{% endif %}
                               class="rounded border-shell-700 bg-shell-900 text-neon-500 focus:ring-neon-500">
                        <span class="text-sm text-shell-200">Auto-build</span>
                    </label>
                    <p class="text-shell-500 text-xs mt-1">Build graph before stages run</p>
                </div>
                <div>
                    <label class="block text-xs font-semibold uppercase tracking-widest text-sand-500 mb-1.5">Graph stale after (minutes)</label>
                    <input type="number" name="graph_stale_minutes" value="{{ crg.graph_stale_minutes }}"
                           class="w-full bg-shell-900 border border-shell-700 rounded-lg px-3 py-2 text-shell-200 text-sm focus:outline-none focus:border-neon-500 transition-colors">
                </div>
            </div>
            <button type="submit" class="btn-primary text-white px-4 py-2 rounded-lg text-sm font-semibold">
                Save CRG Settings
            </button>
        </form>
    </div>
</div>
```

- [ ] **Step 2: Add CRG section to settings.html**

Insert before the Server section in `templates/settings.html`:

```html
    <div class="mt-10 mb-3">
        <h2 class="text-lg font-semibold text-shell-100">Code Review Graph</h2>
        <p class="text-shell-500 text-sm mt-1">Token-efficient code search via CRG</p>
    </div>
    {% include "_crg_field.html" %}
```

- [ ] **Step 3: Add CRG endpoints to settings.py**

```python
@router.get("/settings/crg", response_class=HTMLResponse)
async def get_crg_settings(request: Request, deps: Deps = Depends(get_deps)):
    return get_templates().TemplateResponse(
        request, "_crg_field.html", {"crg": deps.config.crg}
    )

@router.post("/settings/crg", response_class=HTMLResponse)
async def update_crg_settings(request: Request, deps: Deps = Depends(get_deps)):
    form = await get_form_data(request)
    config = deps.config
    config.crg.enabled = bool(form.get("enabled"))
    config.crg.auto_build = bool(form.get("auto_build"))
    stale = str(form.get("graph_stale_minutes", "60")).strip()
    if stale.isdigit():
        config.crg.graph_stale_minutes = int(stale)
    save_config(config, Path(config.repo_path))
    _reload_pipeline(request.app, config)
    return get_templates().TemplateResponse(
        request, "_crg_field.html", {"crg": config.crg, "success": True}
    )
```

- [ ] **Step 4: Run tests**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/ -v -k "not test_run_streaming_yields_stderr" --tb=short`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add templates/_crg_field.html templates/settings.html src/superseded/routes/web/settings.py
git commit -m "feat: add CRG settings UI"
```

---

### Task 6: Run full test suite and lint

**Files:** None (verification only)

- [ ] **Step 1: Run all tests**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/ -v -k "not test_run_streaming_yields_stderr" --tb=short 2>&1 | tail -10`
Expected: ALL PASS

- [ ] **Step 2: Run linter**

Run: `cd /home/debian/workspace/superseded && uv run ruff check src/ tests/`
Expected: No errors

- [ ] **Step 3: Run formatter**

Run: `cd /home/debian/workspace/superseded && uv run ruff format src/ tests/`
Expected: No changes needed

- [ ] **Step 4: Commit if formatter made changes**

```bash
git add -A
git commit -m "chore: format CRG integration changes"
```
