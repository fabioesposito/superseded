# Phase 0: First-Run UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `uv run superseded init && uv run superseded` works out of the box with minimal configuration, clear validation errors, and a setup wizard in the web UI.

**Architecture:** Add a `cli.py` module for the `init` command, add config validation to `config.py`, add a rules editor and setup wizard to the settings route/template. All changes extend existing patterns — no refactoring.

**Tech Stack:** Python 3.14, FastAPI, Pydantic, Jinja2, HTMX, Alpine.js, pytest

---

## File Structure

| File | Action | Purpose |
|------|--------|---------|
| `src/superseded/cli.py` | Create | `superseded init` command — scaffold `.superseded/` |
| `src/superseded/config.py` | Modify | Add `validate_config()` with clear error messages |
| `src/superseded/main.py` | Modify | Wire `init` subcommand into CLI, call validation on startup |
| `src/superseded/routes/web/settings.py` | Modify | Add rules editor endpoints, setup wizard data |
| `templates/settings.html` | Modify | Add rules editor section, setup wizard section |
| `templates/_rules_field.html` | Create | Rules editor partial (HTMX) |
| `templates/_setup_wizard.html` | Create | Setup wizard partial (HTMX) |
| `templates/_agent_detection.html` | Create | Agent detection status partial |
| `tests/test_cli.py` | Create | Tests for `superseded init` |
| `tests/test_config_validation.py` | Create | Tests for config validation |
| `tests/test_settings_routes.py` | Modify | Add tests for rules editor and setup wizard |

---

### Task 1: Add config validation to `config.py`

**Files:**
- Modify: `src/superseded/config.py`
- Create: `tests/test_config_validation.py`

- [ ] **Step 1: Write the failing test for config validation**

```python
# tests/test_config_validation.py
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from superseded.config import SupersededConfig, validate_config, load_config


def test_validate_config_empty_agent():
    config = SupersededConfig(default_agent="")
    errors = validate_config(config)
    assert any("default_agent" in e for e in errors)


def test_validate_config_unknown_agent():
    config = SupersededConfig(default_agent="nonexistent")
    errors = validate_config(config)
    assert any("nonexistent" in e for e in errors)


def test_validate_config_valid():
    config = SupersededConfig(default_agent="opencode")
    errors = validate_config(config)
    assert errors == []


def test_validate_config_invalid_timeout():
    config = SupersededConfig(stage_timeout_seconds=-1)
    errors = validate_config(config)
    assert any("timeout" in e.lower() for e in errors)


def test_validate_config_invalid_port():
    config = SupersededConfig(port=99999)
    errors = validate_config(config)
    assert any("port" in e.lower() for e in errors)


def test_load_config_returns_validation_errors():
    with tempfile.TemporaryDirectory() as tmp:
        config = load_config(Path(tmp))
        # Default config should be valid
        errors = validate_config(config)
        assert errors == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_config_validation.py -v`
Expected: FAIL — `ImportError: cannot import name 'validate_config'`

- [ ] **Step 3: Implement `validate_config` in `config.py`**

Add after `save_config` function in `src/superseded/config.py`:

```python
VALID_AGENTS = {"opencode", "claude-code", "codex", "docker"}


def validate_config(config: SupersededConfig) -> list[str]:
    """Return list of validation error messages. Empty list means valid."""
    errors = []
    if not config.default_agent:
        errors.append("default_agent is required. Set it to 'opencode', 'claude-code', 'codex', or 'docker'.")
    elif config.default_agent not in VALID_AGENTS:
        errors.append(
            f"Unknown default_agent: '{config.default_agent}'. "
            f"Valid agents: {', '.join(sorted(VALID_AGENTS))}."
        )
    if config.stage_timeout_seconds < 0:
        errors.append(f"stage_timeout_seconds must be positive, got {config.stage_timeout_seconds}.")
    if not (1 <= config.port <= 65535):
        errors.append(f"port must be 1-65535, got {config.port}.")
    return errors
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_config_validation.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/config.py tests/test_config_validation.py
git commit -m "feat: add config validation with clear error messages"
```

---

### Task 2: Wire validation into startup and CLI

**Files:**
- Modify: `src/superseded/main.py`
- Create: `src/superseded/cli.py`

- [ ] **Step 1: Write the failing test for startup validation**

```python
# tests/test_cli.py
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from superseded.cli import init_command


def test_init_creates_directory_structure():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        init_command(repo)
        assert (repo / ".superseded").is_dir()
        assert (repo / ".superseded" / "config.yaml").is_file()
        assert (repo / ".superseded" / "rules.md").is_file()
        assert (repo / ".superseded" / "issues").is_dir()


def test_init_creates_default_config():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        init_command(repo)
        import yaml
        with open(repo / ".superseded" / "config.yaml") as f:
            config = yaml.safe_load(f)
        assert config["default_agent"] == "opencode"


def test_init_creates_rules_template():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        init_command(repo)
        rules = (repo / ".superseded" / "rules.md").read_text()
        assert "# Project Rules" in rules


def test_init_creates_example_ticket():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        init_command(repo)
        issues = list((repo / ".superseded" / "issues").glob("*.md"))
        assert len(issues) == 1
        content = issues[0].read_text()
        assert "title:" in content


def test_init_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        init_command(repo)
        init_command(repo)  # should not fail
        assert (repo / ".superseded" / "config.yaml").is_file()


def test_init_preserves_existing_config():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        config_dir = repo / ".superseded"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("port: 9000\n")
        init_command(repo)
        import yaml
        with open(config_dir / "config.yaml") as f:
            config = yaml.safe_load(f)
        assert config["port"] == 9000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'superseded.cli'`

- [ ] **Step 3: Create `src/superseded/cli.py`**

```python
from __future__ import annotations

from pathlib import Path

import yaml


RULES_TEMPLATE = """\
# Project Rules

Rules injected into every agent prompt. Keep these short and non-negotiable.

## Code Style
- Use type hints on all function signatures
- No comments unless explicitly requested

## Testing
- Write tests for new functionality
- Run existing tests before committing

## Security
- Never commit secrets or API keys
- Validate all user input
"""

EXAMPLE_TICKET = """\
---
title: Example ticket
status: new
stage: spec
created: 2026-01-01
---

Describe what you want to build here. The pipeline will take it through
spec → plan → build → verify → review → ship.
"""


def init_command(repo_path: Path) -> None:
    """Scaffold .superseded/ directory with config, rules, and example ticket."""
    superseded_dir = repo_path / ".superseded"
    superseded_dir.mkdir(parents=True, exist_ok=True)

    config_file = superseded_dir / "config.yaml"
    if not config_file.exists():
        default_config = {"default_agent": "opencode", "port": 8000}
        with open(config_file, "w") as f:
            yaml.dump(default_config, f, default_flow_style=False, sort_keys=False)

    rules_file = superseded_dir / "rules.md"
    if not rules_file.exists():
        rules_file.write_text(RULES_TEMPLATE)

    issues_dir = superseded_dir / "issues"
    issues_dir.mkdir(exist_ok=True)

    existing_tickets = list(issues_dir.glob("*.md"))
    if not existing_tickets:
        (issues_dir / "SUP-001-example.md").write_text(EXAMPLE_TICKET)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_cli.py -v`
Expected: ALL PASS

- [ ] **Step 5: Wire `init` subcommand into `main.py` CLI**

Modify `src/superseded/main.py` `cli()` function:

```python
def cli() -> None:
    parser = argparse.ArgumentParser(description="Superseded - local-first agentic pipeline tool")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="Initialize .superseded/ in current directory")
    init_parser.add_argument("repo_path", nargs="?", default=".", help="Path to the git repository")

    run_parser = subparsers.add_parser("run", help="Start the server (default)")
    run_parser.add_argument("repo_path", nargs="?", default=".", help="Path to the git repository")
    run_parser.add_argument("--port", type=int, default=None, help="Port to run the server on")
    run_parser.add_argument("--host", type=str, default=None, help="Host to bind to")

    # Also accept bare repo_path for backwards compatibility
    parser.add_argument("bare_repo_path", nargs="?", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--host", type=str, default=None, help=argparse.SUPPRESS)

    args = parser.parse_args()

    if args.command == "init":
        from superseded.cli import init_command
        init_command(Path(args.repo_path).resolve())
        print(f"Initialized .superseded/ in {args.repo_path}")
        return

    # Default: run server (backwards compatible)
    repo_path = getattr(args, "repo_path", None) or getattr(args, "bare_repo_path", None) or "."
    config = load_config(Path(repo_path).resolve())

    from superseded.config import validate_config
    errors = validate_config(config)
    if errors:
        print("Configuration errors:")
        for err in errors:
            print(f"  - {err}")
        print(f"\nRun 'superseded init' or edit .superseded/config.yaml to fix.")
        return

    port = getattr(args, "port", None) or config.port
    host = getattr(args, "host", None) or config.host

    import uvicorn
    uvicorn.run("superseded.main:create_app", host=host, port=port, factory=True, reload=False)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_cli.py -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add src/superseded/cli.py src/superseded/main.py tests/test_cli.py
git commit -m "feat: add superseded init command with config validation on startup"
```

---

### Task 3: Add rules editor to Settings UI

**Files:**
- Modify: `src/superseded/routes/web/settings.py`
- Modify: `templates/settings.html`
- Create: `templates/_rules_field.html`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_settings_routes.py`:

```python
async def test_settings_page_shows_rules_editor(tmp_repo):
    client, _ = await _make_client(tmp_repo)
    async with client:
        response = await client.get("/settings")
        assert response.status_code == 200
        assert "Project Rules" in response.text


async def test_save_rules(tmp_repo):
    client, _ = await _make_client(tmp_repo)
    async with client:
        token = await _get_csrf(client)
        response = await client.post(
            "/settings/rules",
            data={"rules": "# My Rules\n- Be nice"},
            headers={"X-CSRF-Token": token},
        )
        assert response.status_code == 200
        assert "saved" in response.text.lower()


async def test_rules_persisted_to_file(tmp_repo):
    client, app = await _make_client(tmp_repo)
    async with client:
        token = await _get_csrf(client)
        await client.post(
            "/settings/rules",
            data={"rules": "# Custom Rules\nNo tests allowed"},
            headers={"X-CSRF-Token": token},
        )
        rules_path = Path(tmp_repo) / ".superseded" / "rules.md"
        assert rules_path.exists()
        assert "Custom Rules" in rules_path.read_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_settings_routes.py -v -k "rules"`
Expected: FAIL — "Project Rules" not in response text

- [ ] **Step 3: Add rules editor endpoint to `settings.py`**

Add to `src/superseded/routes/web/settings.py`:

```python
@router.get("/settings/rules", response_class=HTMLResponse)
async def get_rules_editor(request: Request, deps: Deps = Depends(get_deps)):
    rules_path = Path(deps.config.repo_path) / ".superseded" / "rules.md"
    rules_content = ""
    if rules_path.exists():
        rules_content = rules_path.read_text()
    return get_templates().TemplateResponse(
        request,
        "_rules_field.html",
        {"rules": rules_content},
    )


@router.post("/settings/rules", response_class=HTMLResponse)
async def save_rules(request: Request, deps: Deps = Depends(get_deps)):
    form = await get_form_data(request)
    rules_content = str(form.get("rules", ""))
    rules_path = Path(deps.config.repo_path) / ".superseded" / "rules.md"
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(rules_content)
    return get_templates().TemplateResponse(
        request,
        "_rules_field.html",
        {"rules": rules_content, "success": True},
    )
```

- [ ] **Step 4: Create `templates/_rules_field.html`**

```html
<div id="rules-config">
    {% if success %}
    <div class="mb-4 px-5 py-3 text-sm text-olive-400 bg-olive-900/20 rounded-lg border border-olive-800/30">
        Rules saved successfully.
    </div>
    {% endif %}
    <div class="card rounded-xl p-6">
        <form hx-post="/settings/rules" hx-target="#rules-config" hx-swap="outerHTML">
            <div class="mb-4">
                <label class="block text-xs font-semibold uppercase tracking-widest text-sand-500 mb-1.5">Project Rules</label>
                <textarea name="rules" rows="12"
                          class="w-full bg-shell-900 border border-shell-700 rounded-lg px-3 py-2 text-shell-200 text-sm font-mono focus:outline-none focus:border-neon-500 transition-colors"
                          placeholder="# Project Rules&#10;&#10;Rules injected into every agent prompt.">{{ rules | default('') }}</textarea>
                <p class="text-shell-500 text-xs mt-1">These rules are injected into every agent prompt. Keep them short and non-negotiable.</p>
            </div>
            <button type="submit" class="btn-primary text-white px-4 py-2 rounded-lg text-sm font-semibold">
                Save Rules
            </button>
        </form>
    </div>
</div>
```

- [ ] **Step 5: Add rules section to `settings.html`**

Insert before the Server section (before line 85) in `templates/settings.html`:

```html
    <div class="mt-10 mb-3">
        <h2 class="text-lg font-semibold text-shell-100">Project Rules</h2>
        <p class="text-shell-500 text-sm mt-1">Non-negotiable rules injected into every agent prompt</p>
    </div>
    <div hx-get="/settings/rules" hx-trigger="load" hx-swap="outerHTML">
        <div class="card rounded-xl p-6 animate-pulse">
            <div class="h-4 bg-shell-800 rounded w-1/4 mb-4"></div>
            <div class="h-32 bg-shell-800 rounded"></div>
        </div>
    </div>
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_settings_routes.py -v -k "rules"`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add src/superseded/routes/web/settings.py templates/settings.html templates/_rules_field.html tests/test_settings_routes.py
git commit -m "feat: add rules editor to settings UI"
```

---

### Task 4: Add setup wizard to Settings UI

**Files:**
- Modify: `src/superseded/routes/web/settings.py`
- Create: `templates/_setup_wizard.html`
- Create: `templates/_agent_detection.html`
- Modify: `templates/settings.html`
- Modify: `tests/test_settings_routes.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_settings_routes.py`:

```python
async def test_setup_wizard_shows_agent_detection(tmp_repo):
    client, _ = await _make_client(tmp_repo)
    async with client:
        response = await client.get("/settings/setup")
        assert response.status_code == 200
        assert "Setup" in response.text


async def test_setup_wizard_detects_agents(tmp_repo):
    client, _ = await _make_client(tmp_repo)
    async with client:
        response = await client.get("/settings/setup")
        # Should show detection status for known agents
        assert "claude-code" in response.text or "opencode" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_settings_routes.py -v -k "setup_wizard"`
Expected: FAIL — 404 on `/settings/setup`

- [ ] **Step 3: Add setup wizard endpoint to `settings.py`**

```python
import shutil


def _detect_agents() -> list[dict[str, str]]:
    """Detect available CLI agents on the system."""
    agents = [
        {"name": "claude-code", "binary": "claude", "description": "Anthropic Claude Code"},
        {"name": "opencode", "binary": "opencode", "description": "OpenCode CLI"},
        {"name": "codex", "binary": "codex", "description": "OpenAI Codex CLI"},
    ]
    results = []
    for agent in agents:
        found = shutil.which(agent["binary"])
        results.append({
            **agent,
            "available": found is not None,
            "path": found or "not found",
        })
    return results


def _detect_api_keys(config: SupersededConfig) -> list[dict[str, str]]:
    """Check which API keys are configured."""
    keys = [
        {"name": "ANTHROPIC_API_KEY", "configured": bool(config.anthropic_api_key), "agent": "claude-code"},
        {"name": "OPENAI_API_KEY", "configured": bool(config.openai_api_key), "agent": "codex"},
        {"name": "OPENCODE_API_KEY", "configured": bool(config.opencode_api_key), "agent": "opencode"},
        {"name": "GITHUB_TOKEN", "configured": bool(config.github_token), "agent": "ship (PR creation)"},
    ]
    return keys


@router.get("/settings/setup", response_class=HTMLResponse)
async def setup_wizard(request: Request, deps: Deps = Depends(get_deps)):
    agents = _detect_agents()
    api_keys = _detect_api_keys(deps.config)
    return get_templates().TemplateResponse(
        request,
        "_setup_wizard.html",
        {"agents": agents, "api_keys": api_keys},
    )
```

- [ ] **Step 4: Create `templates/_setup_wizard.html`**

```html
<div id="setup-wizard" class="card rounded-xl p-6">
    <h3 class="text-lg font-semibold text-shell-100 mb-4">Agent Detection</h3>
    <div class="space-y-3 mb-6">
        {% for agent in agents %}
        <div class="flex items-center gap-3 text-sm">
            {% if agent.available %}
            <span class="text-olive-400">&#10003;</span>
            {% else %}
            <span class="text-shell-600">&#10007;</span>
            {% endif %}
            <span class="text-shell-200 font-medium">{{ agent.name }}</span>
            <span class="text-shell-500">{{ agent.description }}</span>
            {% if agent.available %}
            <span class="text-shell-600 text-xs ml-auto">{{ agent.path }}</span>
            {% else %}
            <span class="text-shell-600 text-xs ml-auto">not found</span>
            {% endif %}
        </div>
        {% endfor %}
    </div>

    <h3 class="text-lg font-semibold text-shell-100 mb-4">API Keys</h3>
    <div class="space-y-3">
        {% for key in api_keys %}
        <div class="flex items-center gap-3 text-sm">
            {% if key.configured %}
            <span class="text-olive-400">&#10003;</span>
            {% else %}
            <span class="text-shell-600">&#10007;</span>
            {% endif %}
            <span class="text-shell-200 font-medium">{{ key.name }}</span>
            <span class="text-shell-500">for {{ key.agent }}</span>
            {% if key.configured %}
            <span class="text-olive-400 text-xs ml-auto">configured</span>
            {% else %}
            <span class="text-shell-600 text-xs ml-auto">not set</span>
            {% endif %}
        </div>
        {% endfor %}
    </div>
</div>
```

- [ ] **Step 5: Add setup wizard section to `settings.html`**

Insert before the Repositories section (before line 62) in `templates/settings.html`:

```html
    <div class="mt-10 mb-3">
        <h2 class="text-lg font-semibold text-shell-100">Setup</h2>
        <p class="text-shell-500 text-sm mt-1">Detected agents and API key status</p>
    </div>
    <div hx-get="/settings/setup" hx-trigger="load" hx-swap="outerHTML">
        <div class="card rounded-xl p-6 animate-pulse">
            <div class="h-4 bg-shell-800 rounded w-1/4 mb-4"></div>
            <div class="h-20 bg-shell-800 rounded"></div>
        </div>
    </div>
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_settings_routes.py -v -k "setup"`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add src/superseded/routes/web/settings.py templates/settings.html templates/_setup_wizard.html tests/test_settings_routes.py
git commit -m "feat: add setup wizard with agent and API key detection"
```

---

### Task 5: Run full test suite and lint

**Files:** None (verification only)

- [ ] **Step 1: Run all tests**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 2: Run linter**

Run: `cd /home/debian/workspace/superseded && uv run ruff check src/ tests/`
Expected: No errors

- [ ] **Step 3: Run formatter**

Run: `cd /home/debian/workspace/superseded && uv run ruff format src/ tests/`
Expected: No changes needed (already formatted)

- [ ] **Step 4: Final commit if formatter made changes**

```bash
git add -A
git commit -m "chore: format and lint Phase 0 changes"
```
