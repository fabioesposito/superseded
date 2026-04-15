# Architecture Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor 4 architectural weaknesses: add Alembic migrations, switch to agent registry pattern, split routes into api/ and web/ packages, and remove hardcoded model defaults.

**Architecture:** Incremental refactoring — each task is independently testable and deployable. All 258 existing tests must remain green throughout.

**Tech Stack:** Python 3.12+, Alembic, FastAPI, aiosqlite, Pydantic

---

### Task 1: Add Alembic and create initial migration

**Files:**
- Create: `migrations/` (alembic init)
- Create: `migrations/versions/001_initial_schema.py`
- Create: `migrations/env.py` (customize for aiosqlite)
- Create: `migrations/alembic.ini` (project-relative config)
- Modify: `pyproject.toml` (add alembic dependency)
- Modify: `src/superseded/db.py` (remove DDL, call alembic upgrade head)
- Modify: `src/superseded/main.py` (call db migrations at startup)

**Step 1: Add alembic dependency**

In `pyproject.toml`, add `"alembic>=1.13.0"` to the `dependencies` list.

**Step 2: Initialize Alembic**

Run `cd /home/debian/workspace/superseded && uv run alembic init migrations`

Then edit `alembic.ini` (generated in project root) to set `sqlalchemy.url` to a placeholder — we'll override it programmatically.

**Step 3: Customize migrations/env.py**

Replace the generated `migrations/env.py` with a custom version that:
- Uses the `Database` class's `db_path` from config to construct the SQLite URL
- Sets `target_metadata = None` (we're not using SQLAlchemy ORM, just Alembic for DDL)
- Imports `from superseded.config import load_config` to get the repo path

```python
from alembic import context
from pathlib import Path

config = context.config

def run_migrations_online():
    import os
    repo_path = os.environ.get("SUPERSEDED_REPO_PATH", str(Path.cwd()))
    db_path = Path(repo_path) / ".superseded" / "state.db"
    url = f"sqlite:///{db_path}"
    config.set_main_option("sqlalchemy.url", url)

    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import engine_from_config, pool

    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()

def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=None, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

**Step 4: Create initial migration**

Create `migrations/versions/001_initial_schema.py` capturing all 5 tables exactly as currently defined in `db.py:34-93` (issues, stage_results with repo column, harness_iterations with repo column, session_turns, agent_events). Use `op.create_table` and `op.add_column` to match the current schema including the migration columns (repo, pause_reason, started_at, finished_at).

**Step 5: Replace DDL in db.py**

In `db.py`, modify `Database.initialize()`:
- Remove the `CREATE TABLE IF NOT EXISTS` block and the `ALTER TABLE` migration list
- Remove the `executescript` and manual `ALTER TABLE` try/except blocks
- Add a method `run_migrations(self)` that calls `alembic.command.upgrade(alembic_config, "head")` using the synchronous SQLite path
- Call `self.run_migrations()` inside `initialize()` after opening the connection and setting WAL mode
- Keep the connection setup (`aiosqlite.connect`, WAL mode) in `initialize()`

```python
async def initialize(self) -> None:
    if self._conn is not None:
        return
    Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(self.db_path)
    self._conn = conn
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.commit()
    self._run_migrations_sync()
```

Add a synchronous migration method:

```python
def _run_migrations_sync(self) -> None:
    import alembic.config
    from pathlib import Path

    alembic_cfg = alembic.config.Config()
    migrations_dir = Path(__file__).parent.parent.parent.parent / "migrations"
    alembic_cfg.set_main_option("script_location", str(migrations_dir))
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{self.db_path}")

    import alembic.command
    alembic.command.upgrade(alembic_cfg, "head")
```

**Step 6: Update lifespan in main.py**

No changes needed — `db.initialize()` is already called in `lifespan()` and it will now include migrations.

**Step 7: Run tests**

Run: `uv run pytest tests/ -v`
Expected: All 258 tests pass (the migration should produce identical schema)

**Step 8: Commit**

```bash
git add pyproject.toml migrations/ src/superseded/db.py
git commit -m "feat: add Alembic migrations, replace manual DDL in Database.initialize()"
```

---

### Task 2: Agent registry pattern

**Files:**
- Modify: `src/superseded/agents/__init__.py` (add registry + register decorator)
- Modify: `src/superseded/agents/claude_code.py` (add @register_agent)
- Modify: `src/superseded/agents/opencode.py` (add @register_agent)
- Modify: `src/superseded/agents/codex.py` (add @register_agent)
- Modify: `src/superseded/agents/factory.py` (use registry in create())
- Modify: `tests/test_agents.py` (add registry tests, update factory unknown test)

**Step 1: Update agents/__init__.py**

```python
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from superseded.agents.base import SubprocessAgentAdapter

_registry: dict[str, type[SubprocessAgentAdapter]] = {}


def register_agent(name: str):
    def decorator(cls):
        _registry[name] = cls
        return cls
    return decorator


def get_registry() -> dict[str, type[SubprocessAgentAdapter]]:
    return _registry


from superseded.agents import claude_code, codex, opencode  # noqa: F401,E402
```

**Step 2: Add @register_agent to each adapter**

In `claude_code.py`:
```python
from superseded.agents import register_agent

@register_agent("claude-code")
class ClaudeCodeAdapter(SubprocessAgentAdapter):
    ...
```

In `opencode.py`:
```python
from superseded.agents import register_agent

@register_agent("opencode")
class OpenCodeAdapter(SubprocessAgentAdapter):
    ...
```

In `codex.py`:
```python
from superseded.agents import register_agent

@register_agent("codex")
class CodexAdapter(SubprocessAgentAdapter):
    ...
```

**Step 3: Simplify AgentFactory.create()**

In `factory.py`:
```python
from __future__ import annotations

from superseded.agents import get_registry
from superseded.agents.base import AgentAdapter


class AgentFactory:
    def __init__(
        self,
        default_agent: str = "claude-code",
        default_model: str = "",
        timeout: int = 600,
        github_token: str = "",
        openai_api_key: str = "",
        anthropic_api_key: str = "",
        opencode_api_key: str = "",
    ) -> None:
        self.default_agent = default_agent
        self.default_model = default_model
        self.timeout = timeout
        self.github_token = github_token
        self.openai_api_key = openai_api_key
        self.anthropic_api_key = anthropic_api_key
        self.opencode_api_key = opencode_api_key

    def create(self, cli: str | None = None, model: str | None = None) -> AgentAdapter:
        cli = cli or self.default_agent
        model = model or self.default_model
        registry = get_registry()
        if cli not in registry:
            raise ValueError(f"Unknown agent: {cli}")
        api_key_map = {
            "claude-code": self.anthropic_api_key,
            "opencode": self.opencode_api_key,
            "codex": self.openai_api_key,
        }
        return registry[cli](
            model=model,
            timeout=self.timeout,
            github_token=self.github_token,
            api_key=api_key_map.get(cli, ""),
        )
```

**Step 4: Update tests**

In `tests/test_agents.py`:
- Update `test_factory_unknown_cli` to expect `"Unknown agent: bad"` (changed error message)
- Add new tests:

```python
def test_registry_contains_all_agents():
    from superseded.agents import get_registry
    registry = get_registry()
    assert "claude-code" in registry
    assert "opencode" in registry
    assert "codex" in registry

def test_registry_creates_correct_types():
    from superseded.agents import get_registry
    registry = get_registry()
    assert registry["claude-code"] is ClaudeCodeAdapter
    assert registry["opencode"] is OpenCodeAdapter
    assert registry["codex"] is CodexAdapter
```

**Step 5: Run tests**

Run: `uv run pytest tests/test_agents.py -v`
Expected: All agent tests pass

**Step 6: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All 258+ tests pass

**Step 7: Commit**

```bash
git add src/superseded/agents/ tests/test_agents.py
git commit -m "feat: replace agent factory if/elif with registry pattern"
```

---

### Task 3: Remove hardcoded model defaults

**Files:**
- Modify: `src/superseded/config.py` (change defaults to "")
- Modify: `src/superseded/agents/claude_code.py` (add DEFAULT_MODEL)
- Modify: `src/superseded/agents/opencode.py` (no DEFAULT_MODEL, omit --model when empty)
- Modify: `src/superseded/agents/codex.py` (add DEFAULT_MODEL)
- Modify: `src/superseded/routes/settings.py` (remove hardcoded fallback strings)
- Modify: `tests/test_config.py` (update default assertions)
- Modify: `tests/test_agents.py` (update default model assertions)

**Step 1: Update config.py defaults**

Change:
```python
class StageAgentConfig(BaseModel):
    cli: str = "opencode"
    model: str = ""  # was "opencode-go/kimi-k2.5"

class SupersededConfig(BaseModel):
    default_model: str = ""  # was "opencode-go/kimi-k2.5"
```

**Step 2: Add DEFAULT_MODEL to adapters**

In `claude_code.py`:
```python
@register_agent("claude-code")
class ClaudeCodeAdapter(SubprocessAgentAdapter):
    DEFAULT_MODEL = "claude-sonnet-4-20250514"

    def _build_command(self, prompt: str) -> list[str]:
        cmd = ["claude", "-p", prompt, "--output-format", "text"]
        model = self.model or self.DEFAULT_MODEL
        cmd.extend(["--model", model])
        return cmd
```

In `codex.py`:
```python
@register_agent("codex")
class CodexAdapter(SubprocessAgentAdapter):
    DEFAULT_MODEL = "o4-mini"

    def _build_command(self, prompt: str) -> list[str]:
        cmd = ["codex", "--quiet"]
        model = self.model or self.DEFAULT_MODEL
        cmd.extend(["--model", model])
        return cmd
```

In `opencode.py` — keep current behavior (omit `-m` when no model):
```python
@register_agent("opencode")
class OpenCodeAdapter(SubprocessAgentAdapter):

    def _build_command(self, prompt: str) -> list[str]:
        cmd = ["opencode"]
        if self.model:
            cmd.extend(["-m", self.model])
        cmd.extend(["run", "--pure"])
        cmd.append(prompt)
        return cmd
```

**Step 3: Update settings.py**

Remove hardcoded `"opencode-go/kimi-k2.5"` fallbacks from the `update_agents` route. Change:
```python
model=str(form.get("spec_model", "opencode-go/kimi-k2.5")),
```
to:
```python
model=str(form.get("spec_model", "")),
```
for all 6 stages.

**Step 4: Update test assertions**

In `tests/test_config.py`:
- `test_stage_agent_config_defaults`: change `assert cfg.model == "opencode-go/kimi-k2.5"` to `assert cfg.model == ""`
- `test_superseded_config_stages_default`: change `assert cfg.default_model == "opencode-go/kimi-k2.5"` to `assert cfg.default_model == ""`

In `tests/test_agents.py`:
- `test_claude_code_no_model`: update assertion — now includes `--model claude-sonnet-4-20250514`
- `test_codex_no_model`: update assertion — now includes `--model o4-mini`
- `test_factory_default`: `assert agent.model == ""` stays (factory default_model is now "")

**Step 5: Run tests**

Run: `uv run pytest tests/test_config.py tests/test_agents.py -v`
Then: `uv run pytest tests/ -v`
Expected: All tests pass

**Step 6: Commit**

```bash
git add src/superseded/config.py src/superseded/agents/ src/superseded/routes/settings.py tests/
git commit -m "feat: remove hardcoded model defaults, use per-adapter DEFAULT_MODEL"
```

---

### Task 4: Split routes into api/ and web/ packages

**Files:**
- Create: `src/superseded/routes/api/__init__.py`
- Create: `src/superseded/routes/api/pipeline.py`
- Create: `src/superseded/routes/web/__init__.py`
- Create: `src/superseded/routes/web/dashboard.py`
- Create: `src/superseded/routes/web/issues.py`
- Create: `src/superseded/routes/web/pipeline.py`
- Create: `src/superseded/routes/web/settings.py`
- Modify: `src/superseded/routes/deps.py` (add shared helpers)
- Modify: `src/superseded/main.py` (update imports)
- Delete: `src/superseded/routes/pipeline.py` (content split to api + web)
- Delete: `src/superseded/routes/dashboard.py` (moved to web/)
- Delete: `src/superseded/routes/issues.py` (moved to web/)
- Delete: `src/superseded/routes/settings.py` (moved to web/)

**Step 1: Add shared helpers to deps.py**

Move `_find_issue`, `_get_executor`, `_get_event_manager`, `_running`, `_render_issue_detail_oob`, `_render_running_indicator`, and `_run_stage_background` from `pipeline.py` into `deps.py`. The `_run_and_advance` function referenced in `issues.py` doesn't exist yet — implement it as a helper that advances a stage and returns the updated HTML content.

```python
# In deps.py, add:
_running: set[str] = set()

def _find_issue(deps: Deps, issue_id: str):
    from superseded.tickets.reader import list_issues
    issues_dir = str(Path(deps.config.repo_path) / deps.config.issues_dir)
    matching = [i for i in list_issues(issues_dir) if i.id == issue_id]
    return matching[0] if matching else None

def _get_executor(deps: Deps):
    if deps.pipeline is None:
        raise RuntimeError("Pipeline not initialized")
    return deps.pipeline.executor

def _get_event_manager(deps: Deps):
    if deps.pipeline is None:
        raise RuntimeError("Pipeline not initialized")
    return deps.pipeline.event_manager
```

Move `_render_issue_detail_oob`, `_render_running_indicator`, `_run_stage_background`, and `_run_and_advance` to `deps.py` as well.

**Step 2: Create routes/api/__init__.py**

```python
from superseded.routes.api.pipeline import api_router

__all__ = ["api_router"]
```

**Step 3: Create routes/api/pipeline.py**

Extract JSON/API endpoints from current `pipeline.py`:
- `GET /metrics` (JSON metrics endpoint)
- `GET /issues/{issue_id}/events` (historical events JSON)
- `GET /issues/{issue_id}/events/stream` (SSE stream)

Keep `api_router = APIRouter(prefix="/api/pipeline")` and `_compute_metrics`.

**Step 4: Create routes/web/__init__.py**

Empty or minimal — just a package marker.

**Step 5: Create routes/web/dashboard.py**

Move current `dashboard.py` content (with `router = APIRouter()`, prefix `/`).

**Step 6: Create routes/web/issues.py**

Move current `issues.py` content, but update imports:
- Change `from superseded.routes.pipeline import _run_and_advance` → `from superseded.routes.deps import _run_and_advance`

**Step 7: Create routes/web/pipeline.py**

Move HTML/HTMX pipeline routes from current `pipeline.py`:
- `POST /pipeline/issues/{id}/advance`
- `POST /pipeline/issues/{id}/retry`
- `GET /pipeline/issues/{id}/status`
- `GET /pipeline/sse/dashboard`
- `GET /pipeline/metrics` (HTML dashboard)

Import shared helpers from `deps.py`.

**Step 8: Create routes/web/settings.py**

Move current `settings.py` content with updated `_reload_pipeline` import path.

**Step 9: Update main.py**

Change imports:
```python
# Replace:
from superseded.routes.pipeline import api_router as pipeline_api_router
from superseded.routes.pipeline import router as pipeline_router
# With:
from superseded.routes.api.pipeline import api_router as pipeline_api_router
from superseded.routes.web.pipeline import router as pipeline_router

# Replace:
from superseded.routes.dashboard import router as dashboard_router
# With:
from superseded.routes.web.dashboard import router as dashboard_router

# Replace:
from superseded.routes.issues import router as issues_router
# With:
from superseded.routes.web.issues import router as issues_router

# Replace:
from superseded.routes.settings import router as settings_router
# With:
from superseded.routes.web.settings import router as settings_router
```

**Step 10: Delete old route files**

Remove `src/superseded/routes/pipeline.py`, `src/superseded/routes/dashboard.py`, `src/superseded/routes/issues.py`, `src/superseded/routes/settings.py`.

**Step 11: Implement `_run_and_advance`**

This function is imported but doesn't exist yet. Create it in `deps.py`. It should:
1. Run a stage via the executor
2. Update issue status in DB and filesystem
3. Return an HTML response with the updated issue detail content

**Step 12: Run tests**

Run: `uv run pytest tests/ -v`
Expected: All 258+ tests pass

**Step 13: Run linter**

Run: `uv run ruff check src/ tests/`
Expected: No errors

**Step 14: Commit**

```bash
git add src/superseded/routes/ src/superseded/main.py
git add -u  # stage deletions of old files
git commit -m "refactor: split routes into api/ and web/ packages"
```

---

### Task 5: Final verification

**Step 1: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests pass

**Step 2: Run linter and formatter**

Run: `uv run ruff check src/ tests/ && uv run ruff format src/ tests/ --check`
Expected: Clean

**Step 3: Run app smoke test**

Run: `uv run superseded` (start server briefly, verify it starts without error)
Expected: Server starts on port 8000

**Step 4: Commit any remaining fixes**

```bash
git add -A
git commit -m "chore: final verification fixes"
```