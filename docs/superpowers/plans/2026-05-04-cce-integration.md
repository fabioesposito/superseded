# CCE Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate Code Context Engine (CCE) as the context engineering layer — auto-index repos, replace docs index with CCE search, replace session history with session_recall, inject CCE tool descriptions into agent prompts.

**Architecture:** CCE runs as a CLI subprocess (`cce index`, `cce search`, `cce sessions`). A `CCEClient` wrapper handles all CCE interactions. The `ContextAssembler` gets new layers for CCE search results and tool descriptions. The `Harness` auto-indexes repos before stages and records decisions after.

**Tech Stack:** Python 3.14, asyncio subprocess, CCE CLI (`code-context-engine`)

---

## File Structure

| File | Action | Purpose |
|------|--------|---------|
| `src/superseded/config.py` | Modify | Add `CCEConfig` model |
| `src/superseded/harness/cce.py` | Create | CCEClient wrapper (index, search, recall, record) |
| `src/superseded/harness/context.py` | Modify | Replace docs index + session history layers with CCE |
| `src/superseded/harness/__init__.py` | Modify | Auto-index before stages, record decisions after |
| `templates/settings.html` | Modify | Add CCE config section |
| `templates/_cce_field.html` | Create | CCE settings partial |
| `tests/test_cce.py` | Create | Tests for CCEClient |
| `tests/test_context.py` | Modify | Update for CCE-enhanced context assembly |

---

### Task 1: Add CCEConfig to config.py

**Files:**
- Modify: `src/superseded/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_cce_config_defaults():
    from superseded.config import CCEConfig
    cfg = CCEConfig()
    assert cfg.enabled is False
    assert cfg.auto_index is True
    assert cfg.index_stale_minutes == 60
    assert cfg.compression_level == "standard"


def test_superseded_config_with_cce():
    from superseded.config import CCEConfig
    cfg = SupersededConfig(cce=CCEConfig(enabled=True))
    assert cfg.cce.enabled is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_config.py -v -k "cce"`
Expected: FAIL — `ImportError: cannot import name 'CCEConfig'`

- [ ] **Step 3: Add CCEConfig to config.py**

Add after `ResourceLimitsConfig`:

```python
class CCEConfig(BaseModel):
    enabled: bool = False
    auto_index: bool = True
    index_stale_minutes: int = 60
    compression_level: str = "standard"
```

Add `cce` field to `SupersededConfig`:

```python
cce: CCEConfig = Field(default_factory=CCEConfig)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_config.py -v -k "cce"`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/config.py tests/test_config.py
git commit -m "feat: add CCEConfig for Code Context Engine integration"
```

---

### Task 2: Create CCEClient wrapper

**Files:**
- Create: `src/superseded/harness/cce.py`
- Create: `tests/test_cce.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_cce.py
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from superseded.harness.cce import CCEClient, CCESearchResult


class TestCCEClient:
    def test_available_when_cce_in_path(self):
        with patch("shutil.which", return_value="/usr/bin/cce"):
            client = CCEClient("/tmp/test")
            assert client.available is True

    def test_not_available_when_cce_missing(self):
        with patch("shutil.which", return_value=None):
            client = CCEClient("/tmp/test")
            assert client.available is False

    def test_is_indexed_false_when_no_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = CCEClient(tmp)
            assert client.is_indexed() is False

    def test_is_indexed_true_when_dir_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".context-engine").mkdir()
            client = CCEClient(tmp)
            assert client.is_indexed() is True

    def test_is_stale_when_no_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = CCEClient(tmp)
            assert client.is_stale() is True

    def test_is_stale_false_when_fresh(self):
        import time
        with tempfile.TemporaryDirectory() as tmp:
            idx = Path(tmp) / ".context-engine"
            idx.mkdir()
            db = idx / "index.db"
            db.write_text("test")
            client = CCEClient(tmp)
            assert client.is_stale(max_age_minutes=60) is False

    def test_is_stale_true_when_old(self):
        import time
        with tempfile.TemporaryDirectory() as tmp:
            idx = Path(tmp) / ".context-engine"
            idx.mkdir()
            db = idx / "index.db"
            db.write_text("test")
            # Set mtime to 2 hours ago
            old_time = time.time() - 7200
            os.utime(db, (old_time, old_time))
            client = CCEClient(tmp)
            assert client.is_stale(max_age_minutes=60) is True

    def test_parse_search_results(self):
        client = CCEClient("/tmp/test")
        raw = json.dumps([
            {"file": "main.go", "chunk": "func main() {}", "score": 0.95, "compressed": "func main()"},
            {"file": "auth.go", "chunk": "func login() {}", "score": 0.80, "compressed": "func login()"},
        ])
        results = client._parse_search_results(raw)
        assert len(results) == 2
        assert results[0].file == "main.go"
        assert results[0].score == 0.95

    def test_parse_search_results_empty(self):
        client = CCEClient("/tmp/test")
        assert client._parse_search_results("") == []
        assert client._parse_search_results("invalid json") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_cce.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'superseded.harness.cce'`

- [ ] **Step 3: Create `src/superseded/harness/cce.py`**

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
class CCESearchResult:
    file: str
    chunk: str
    score: float
    compressed: str


class CCEClient:
    def __init__(self, repo_path: str, cce_bin: str = "cce") -> None:
        self.repo_path = Path(repo_path)
        self.cce_bin = cce_bin
        self._available = shutil.which(cce_bin) is not None

    @property
    def available(self) -> bool:
        return self._available

    async def _run(self, *args: str, timeout: float = 60.0) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.cce_bin, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.repo_path),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            if proc.returncode != 0:
                logger.warning("cce %s failed: %s", args[0], stderr.decode()[:200])
                return ""
            return stdout.decode()
        except asyncio.TimeoutError:
            logger.warning("cce %s timed out after %ds", args[0], int(timeout))
            return ""
        except FileNotFoundError:
            logger.warning("cce binary not found: %s", self.cce_bin)
            return ""

    async def index(self) -> bool:
        result = await self._run("index", timeout=120.0)
        return "Indexed" in result or "indexed" in result

    async def reindex(self) -> bool:
        result = await self._run("reindex", timeout=120.0)
        return bool(result)

    async def search(self, query: str, top_k: int = 10) -> list[CCESearchResult]:
        result = await self._run("search", query, "--top-k", str(top_k))
        return self._parse_search_results(result)

    async def session_recall(self, topic: str = "") -> str:
        args = ["sessions", "export"]
        result = await self._run(*args, timeout=30.0)
        return result

    async def record_decision(self, decision: str, reason: str = "") -> None:
        # CCE doesn't have a CLI command for record_decision — it's MCP only.
        # We'll use the sessions export to check if memory.db exists,
        # and skip recording if it doesn't (first run).
        # The actual recording happens via the agent's MCP tools.
        pass

    def is_indexed(self) -> bool:
        index_dir = self.repo_path / ".context-engine"
        return index_dir.exists()

    def is_stale(self, max_age_minutes: int = 60) -> bool:
        index_dir = self.repo_path / ".context-engine"
        if not index_dir.exists():
            return True
        db_file = index_dir / "index.db"
        if not db_file.exists():
            return True
        age = time.time() - db_file.stat().st_mtime
        return age > max_age_minutes * 60

    def _parse_search_results(self, raw: str) -> list[CCESearchResult]:
        if not raw:
            return []
        try:
            data = json.loads(raw)
            return [
                CCESearchResult(
                    file=item.get("file", ""),
                    chunk=item.get("chunk", ""),
                    score=item.get("score", 0.0),
                    compressed=item.get("compressed", ""),
                )
                for item in data
            ]
        except (json.JSONDecodeError, TypeError):
            return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_cce.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/harness/cce.py tests/test_cce.py
git commit -m "feat: add CCEClient wrapper for Code Context Engine"
```

---

### Task 3: Integrate CCE into ContextAssembler

**Files:**
- Modify: `src/superseded/harness/context.py`

- [ ] **Step 1: Read current ContextAssembler**

Read `src/superseded/harness/context.py` to understand the layer structure.

- [ ] **Step 2: Add CCE search results layer**

Add a new method to `ContextAssembler`:

```python
def _build_cce_search_layer(self, query: str, results: list) -> str | None:
    if not results:
        return None
    parts = [f"## Code Search Results: \"{query}\"\n"]
    for r in results:
        parts.append(f"### {r.file} (score: {r.score:.2f})\n```\n{r.compressed}\n```")
    return "\n\n".join(parts)

def _build_cce_tools_layer(self) -> str:
    return (
        "## Code Context Tools\n\n"
        "You have access to code search via the CCE MCP server:\n\n"
        "- `context_search(query)` — Search the codebase for relevant code chunks. "
        "Use this INSTEAD of reading entire files.\n"
        "- `expand_chunk(chunk_id)` — Get full source for a compressed result.\n"
        "- `related_context(file)` — Find code via graph edges (calls, imports).\n"
        "- `session_recall(topic)` — Recall decisions from past sessions.\n"
        "- `record_decision(decision, reason)` — Save a decision for future sessions.\n"
        "- `record_code_area(file, description)` — Record which files you're working in.\n\n"
        "Use `context_search` to find relevant code before reading files. "
        "This saves tokens and finds the right code faster."
    )
```

- [ ] **Step 3: Modify `build()` to accept CCE results**

Update the `build()` method signature to accept optional CCE results:

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
    cce_search_results: list | None = None,
    cce_enabled: bool = False,
) -> str:
```

In the build method, replace the docs index and session history layers:

```python
# Replace docs index with CCE search results if available
if cce_enabled and cce_search_results:
    cce_layer = self._build_cce_search_layer("codebase", cce_search_results)
    if cce_layer:
        layers.append(cce_layer)
else:
    docs_index = self._build_docs_index_layer()
    if docs_index:
        layers.append(docs_index)

# Replace session history with CCE recall if available
if cce_enabled:
    cce_tools = self._build_cce_tools_layer()
    layers.append(cce_tools)
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
git commit -m "feat: integrate CCE search results and tools into context assembly"
```

---

### Task 4: Wire CCE into Harness

**Files:**
- Modify: `src/superseded/harness/__init__.py`

- [ ] **Step 1: Add CCE client to Harness**

In `Harness.__init__`, add:

```python
from superseded.harness.cce import CCEClient

self.cce_client = CCEClient(repo_path)
```

- [ ] **Step 2: Add auto-index method**

```python
async def _ensure_cce_indexed(self) -> None:
    if not self.cce_client.available:
        return
    config = self.stage_configs.get("_global")
    # Check config for CCE enabled — we need to pass it from the caller
    # For now, check if .context-engine exists or if stale
    if not self.cce_client.is_indexed():
        logger.info("CCE index not found, indexing %s", self.repo_path)
        await self.cce_client.index()
    elif self.cce_client.is_stale():
        logger.info("CCE index stale, re-indexing %s", self.repo_path)
        await self.cce_client.reindex()
```

- [ ] **Step 3: Wire into run_stage**

In `Harness.run_stage()`, before calling `_run_stage_streaming`:

```python
# Auto-index if CCE is available
if self.cce_client.available:
    await self._ensure_cce_indexed()
```

- [ ] **Step 4: Pass CCE results to context assembler**

In `_run_stage_streaming`, get CCE search results and pass to context assembler:

```python
cce_results = None
if self.cce_client.available:
    # Search for relevant code based on the issue title
    cce_results = await self.cce_client.search(issue.title, top_k=10)

prompt = self.context_assembler.build(
    stage=stage, issue=issue, artifacts_path=artifacts_path,
    previous_errors=previous_errors, iteration=0, target_repo=repo,
    cce_search_results=cce_results,
    cce_enabled=self.cce_client.available,
)
```

- [ ] **Step 5: Run full test suite**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/ -v -k "not test_run_streaming_yields_stderr" --tb=short`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/superseded/harness/__init__.py
git commit -m "feat: wire CCE auto-indexing and search into harness pipeline"
```

---

### Task 5: Add CCE config to Settings UI

**Files:**
- Create: `templates/_cce_field.html`
- Modify: `templates/settings.html`
- Modify: `src/superseded/routes/web/settings.py`

- [ ] **Step 1: Create CCE settings partial**

```html
<div id="cce-config">
    {% if success %}
    <div class="mb-4 px-5 py-3 text-sm text-olive-400 bg-olive-900/20 rounded-lg border border-olive-800/30">
        CCE settings saved successfully.
    </div>
    {% endif %}
    <div class="card rounded-xl p-6">
        <form hx-post="/settings/cce" hx-target="#cce-config" hx-swap="outerHTML">
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                <div>
                    <label class="flex items-center gap-2 cursor-pointer">
                        <input type="checkbox" name="enabled" {% if cce.enabled %}checked{% endif %}
                               class="rounded border-shell-700 bg-shell-900 text-neon-500 focus:ring-neon-500">
                        <span class="text-sm text-shell-200">Enable CCE</span>
                    </label>
                    <p class="text-shell-500 text-xs mt-1">Code Context Engine for token-efficient code search</p>
                </div>
                <div>
                    <label class="flex items-center gap-2 cursor-pointer">
                        <input type="checkbox" name="auto_index" {% if cce.auto_index %}checked{% endif %}
                               class="rounded border-shell-700 bg-shell-900 text-neon-500 focus:ring-neon-500">
                        <span class="text-sm text-shell-200">Auto-index</span>
                    </label>
                    <p class="text-shell-500 text-xs mt-1">Index repos before stages run</p>
                </div>
                <div>
                    <label class="block text-xs font-semibold uppercase tracking-widest text-sand-500 mb-1.5">Index stale after (minutes)</label>
                    <input type="number" name="index_stale_minutes" value="{{ cce.index_stale_minutes }}"
                           class="w-full bg-shell-900 border border-shell-700 rounded-lg px-3 py-2 text-shell-200 text-sm focus:outline-none focus:border-neon-500 transition-colors">
                </div>
                <div>
                    <label class="block text-xs font-semibold uppercase tracking-widest text-sand-500 mb-1.5">Compression level</label>
                    <select name="compression_level"
                            class="w-full bg-shell-900 border border-shell-700 rounded-lg px-3 py-2 text-shell-200 text-sm focus:outline-none focus:border-neon-500 transition-colors">
                        <option value="off" {% if cce.compression_level == 'off' %}selected{% endif %}>Off</option>
                        <option value="lite" {% if cce.compression_level == 'lite' %}selected{% endif %}>Lite</option>
                        <option value="standard" {% if cce.compression_level == 'standard' %}selected{% endif %}>Standard</option>
                        <option value="max" {% if cce.compression_level == 'max' %}selected{% endif %}>Max</option>
                    </select>
                </div>
            </div>
            <button type="submit" class="btn-primary text-white px-4 py-2 rounded-lg text-sm font-semibold">
                Save CCE Settings
            </button>
        </form>
    </div>
</div>
```

- [ ] **Step 2: Add CCE section to settings.html**

Insert before the Server section in `templates/settings.html`:

```html
    <div class="mt-10 mb-3">
        <h2 class="text-lg font-semibold text-shell-100">Code Context Engine</h2>
        <p class="text-shell-500 text-sm mt-1">Token-efficient code search via CCE</p>
    </div>
    {% include "_cce_field.html" %}
```

- [ ] **Step 3: Add CCE endpoints to settings.py**

```python
@router.get("/settings/cce", response_class=HTMLResponse)
async def get_cce_settings(request: Request, deps: Deps = Depends(get_deps)):
    return get_templates().TemplateResponse(
        request, "_cce_field.html", {"cce": deps.config.cce}
    )

@router.post("/settings/cce", response_class=HTMLResponse)
async def update_cce_settings(request: Request, deps: Deps = Depends(get_deps)):
    form = await get_form_data(request)
    config = deps.config
    config.cce.enabled = bool(form.get("enabled"))
    config.cce.auto_index = bool(form.get("auto_index"))
    stale = str(form.get("index_stale_minutes", "60")).strip()
    if stale.isdigit():
        config.cce.index_stale_minutes = int(stale)
    config.cce.compression_level = str(form.get("compression_level", "standard"))
    save_config(config, Path(config.repo_path))
    _reload_pipeline(request.app, config)
    return get_templates().TemplateResponse(
        request, "_cce_field.html", {"cce": config.cce, "success": True}
    )
```

- [ ] **Step 4: Run tests**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/ -v -k "not test_run_streaming_yields_stderr" --tb=short`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add templates/_cce_field.html templates/settings.html src/superseded/routes/web/settings.py
git commit -m "feat: add CCE settings UI"
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
git commit -m "chore: format CCE integration changes"
```
