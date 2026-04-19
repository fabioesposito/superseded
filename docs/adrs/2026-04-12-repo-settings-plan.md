---
title: Repo Settings Page Implementation Plan
category: adrs
summary: Repo Settings Page Implementation Plan
tags: []
date: 2026-04-12
---

# Repo Settings Page Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `/settings` web page for managing repository configurations (name, git URL, local path, branch) with CRUD operations persisted to `config.yaml`, and auto-clone behavior when repos are missing.

**Architecture:** New route module `settings.py` handles CRUD via HTMX. Config is read/written through `config.py`. Auto-clone logic added to `worktree.py`. Nav updated in `base.html`.

**Tech Stack:** FastAPI, HTMX, Alpine.js, Tailwind CSS (CDN), Jinja2, YAML config.

---

### Task 1: Add `git_url` field to `RepoEntry` and `save_config` helper

**Files:**
- Modify: `src/superseded/config.py`

**Step 1: Add `git_url` field to `RepoEntry`**

```python
class RepoEntry(BaseModel):
    path: str
    git_url: str = ""
    branch: str = ""
```

**Step 2: Add `save_config` function**

```python
def save_config(config: SupersededConfig, repo_path: Path) -> None:
    config_file = repo_path / ".superseded" / "config.yaml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(exclude={"repo_path"})
    # Remove defaults to keep yaml clean
    data = {k: v for k, v in data.items() if v != SupersededConfig().model_dump().get(k)}
    with open(config_file, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
```

**Step 3: Verify with ruff**

Run: `uv run ruff check src/superseded/config.py`
Expected: No errors

**Step 4: Commit**

```bash
git add src/superseded/config.py
git commit -m "feat: add git_url to RepoEntry and save_config helper"
```

---

### Task 2: Create settings route with GET endpoint

**Files:**
- Create: `src/superseded/routes/settings.py`
- Modify: `src/superseded/main.py` (add router include)

**Step 1: Create `src/superseded/routes/settings.py`**

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from superseded.routes import get_templates
from superseded.routes.deps import Deps, get_deps

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, deps: Deps = Depends(get_deps)):
    repos = deps.config.repos
    return get_templates().TemplateResponse(
        request,
        "settings.html",
        {
            "repos": repos,
        },
    )
```

**Step 2: Register router in `main.py`**

Add import and include:
```python
from superseded.routes.settings import router as settings_router
# ...
app.include_router(settings_router)
```

**Step 3: Verify**

Run: `uv run ruff check src/superseded/routes/settings.py src/superseded/main.py`
Expected: No errors

**Step 4: Commit**

```bash
git add src/superseded/routes/settings.py src/superseded/main.py
git commit -m "feat: add settings route with GET endpoint"
```

---

### Task 3: Create settings template

**Files:**
- Create: `templates/settings.html`

**Step 1: Create `templates/settings.html`**

Template with:
- Page header "Settings" with subtitle "Manage repositories"
- Table listing repos (name, git_url, path, branch) from `repos` dict
- Add repo form (hidden by default, toggled by Alpine.js)
- Edit/delete actions per row via HTMX
- Styled to match existing dark theme (shell/sand/neon palette, card class, Outfit font)

```html
{% extends "base.html" %}
{% block title %}Settings - Superseded{% endblock %}
{% block content %}
<div class="animate-fade-in">
    <div class="flex items-center justify-between mb-8">
        <div>
            <h1 class="text-3xl font-bold text-shell-50 tracking-tight">Settings</h1>
            <p class="text-shell-500 text-sm mt-1">Manage repositories for the pipeline</p>
        </div>
        <button @click="showAddForm = !showAddForm"
                class="btn-primary text-white px-5 py-2.5 rounded-lg text-sm font-semibold inline-flex items-center gap-2">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
            Add Repo
        </button>
    </div>

    <!-- Add repo form -->
    <div x-show="showAddForm" x-cloak
         class="card rounded-xl p-6 mb-6 animate-fade-in"
         x-data="{ name: '', git_url: '', path: '', branch: '' }">
        <h2 class="text-lg font-semibold text-shell-100 mb-4">Add Repository</h2>
        <form hx-post="/settings/repos"
              hx-target="#repos-table"
              hx-swap="outerHTML"
              @htmx:after-request="showAddForm = false">
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                <div>
                    <label class="block text-xs font-semibold uppercase tracking-widest text-sand-500 mb-1.5">Name</label>
                    <input type="text" name="name" x-model="name" required
                           class="w-full bg-shell-900 border border-shell-700 rounded-lg px-3 py-2 text-shell-200 text-sm focus:outline-none focus:border-neon-500 transition-colors"
                           placeholder="e.g. frontend">
                </div>
                <div>
                    <label class="block text-xs font-semibold uppercase tracking-widest text-sand-500 mb-1.5">Git URL</label>
                    <input type="text" name="git_url" x-model="git_url"
                           class="w-full bg-shell-900 border border-shell-700 rounded-lg px-3 py-2 text-shell-200 text-sm focus:outline-none focus:border-neon-500 transition-colors"
                           placeholder="https://github.com/org/repo.git">
                </div>
                <div>
                    <label class="block text-xs font-semibold uppercase tracking-widest text-sand-500 mb-1.5">Local Path</label>
                    <input type="text" name="path" x-model="path" required
                           class="w-full bg-shell-900 border border-shell-700 rounded-lg px-3 py-2 text-shell-200 text-sm focus:outline-none focus:border-neon-500 transition-colors"
                           placeholder="/path/to/repo">
                </div>
                <div>
                    <label class="block text-xs font-semibold uppercase tracking-widest text-sand-500 mb-1.5">Branch</label>
                    <input type="text" name="branch" x-model="branch"
                           class="w-full bg-shell-900 border border-shell-700 rounded-lg px-3 py-2 text-shell-200 text-sm focus:outline-none focus:border-neon-500 transition-colors"
                           placeholder="main">
                </div>
            </div>
            <div class="flex items-center gap-3">
                <button type="submit" class="btn-primary text-white px-4 py-2 rounded-lg text-sm font-semibold">
                    Save Repo
                </button>
                <button type="button" @click="showAddForm = false" class="btn-secondary text-shell-300 px-4 py-2 rounded-lg text-sm font-medium">
                    Cancel
                </button>
            </div>
        </form>
    </div>

    <!-- Repos table -->
    {% include "_repos_table.html" %}
</div>
{% endblock %}
```

**Step 2: Create `templates/_repos_table.html` partial**

```html
<div id="repos-table" class="card rounded-xl overflow-x-auto">
    <table class="w-full min-w-[640px]">
        <thead>
            <tr class="border-b border-shell-700/50">
                <th class="px-5 py-3.5 text-left text-xs font-semibold uppercase tracking-widest text-sand-500">Name</th>
                <th class="px-5 py-3.5 text-left text-xs font-semibold uppercase tracking-widest text-sand-500">Git URL</th>
                <th class="px-5 py-3.5 text-left text-xs font-semibold uppercase tracking-widest text-sand-500">Path</th>
                <th class="px-5 py-3.5 text-left text-xs font-semibold uppercase tracking-widest text-sand-500">Branch</th>
                <th class="px-5 py-3.5 text-right text-xs font-semibold uppercase tracking-widest text-sand-500">Actions</th>
            </tr>
        </thead>
        <tbody>
            {% for name, entry in repos.items() %}
            <tr class="border-t border-shell-800/40 hover:bg-shell-900/50 transition-colors group"
                id="repo-row-{{ name }}">
                {% include "_repo_row.html" %}
            </tr>
            {% endfor %}
            {% if not repos %}
            <tr>
                <td colspan="5" class="px-5 py-16 text-center">
                    <div class="text-shell-500 text-sm mb-3">No repositories configured</div>
                    <button @click="showAddForm = true" class="text-neon-400 hover:text-neon-300 text-sm font-medium transition-colors">Add your first repo &rarr;</button>
                </td>
            </tr>
            {% endif %}
        </tbody>
    </table>
</div>
```

**Step 3: Create `templates/_repo_row.html` partial**

```html
<td class="px-5 py-3.5 font-mono text-sm text-neon-400">{{ name }}</td>
<td class="px-5 py-3.5 text-shell-300 text-sm font-mono truncate max-w-[280px]">{{ entry.git_url or "—" }}</td>
<td class="px-5 py-3.5 text-shell-400 text-sm font-mono truncate max-w-[240px]">{{ entry.path }}</td>
<td class="px-5 py-3.5 text-shell-400 text-sm">{{ entry.branch or "—" }}</td>
<td class="px-5 py-3.5 text-right">
    <button hx-delete="/settings/repos/{{ name }}"
            hx-target="#repos-table"
            hx-swap="outerHTML"
            hx-confirm="Remove repo '{{ name }}'?"
            class="text-shell-600 hover:text-coral-400 transition-colors text-sm">
        Remove
    </button>
</td>
```

**Step 4: Verify templates render**

Run: `uv run ruff check templates/` (won't apply, but check Python files)
Run: `uv run superseded` and visit `/settings` in browser
Expected: Settings page renders with table and add form

**Step 5: Commit**

```bash
git add templates/settings.html templates/_repos_table.html templates/_repo_row.html
git commit -m "feat: add settings page template with repo table and add form"
```

---

### Task 4: Add POST/DELETE endpoints for repo CRUD

**Files:**
- Modify: `src/superseded/routes/settings.py`

**Step 1: Add POST endpoint to create a repo**

```python
from fastapi import Form
from superseded.config import RepoEntry, save_config
from pathlib import Path


@router.post("/settings/repos", response_class=HTMLResponse)
async def add_repo(
    request: Request,
    deps: Deps = Depends(get_deps),
    name: str = Form(...),
    git_url: str = Form(""),
    path: str = Form(...),
    branch: str = Form(""),
):
    config = deps.config
    config.repos[name] = RepoEntry(path=path, git_url=git_url, branch=branch)
    save_config(config, Path(config.repo_path))
    _reload_pipeline(request.app, config)
    repos = config.repos
    return get_templates().TemplateResponse(
        request, "_repos_table.html", {"repos": repos}
    )
```

**Step 2: Add DELETE endpoint**

```python
@router.delete("/settings/repos/{repo_name}", response_class=HTMLResponse)
async def delete_repo(
    request: Request,
    repo_name: str,
    deps: Deps = Depends(get_deps),
):
    config = deps.config
    config.repos.pop(repo_name, None)
    save_config(config, Path(config.repo_path))
    _reload_pipeline(request.app, config)
    repos = config.repos
    return get_templates().TemplateResponse(
        request, "_repos_table.html", {"repos": repos}
    )
```

**Step 3: Add pipeline reload helper**

```python
from superseded.main import _build_pipeline_state


def _reload_pipeline(app, config):
    app.state.config = config
    pipeline = _build_pipeline_state(config)
    pipeline.executor.db = app.state.db
    app.state.pipeline = pipeline
```

**Step 4: Verify with ruff and test**

Run: `uv run ruff check src/superseded/routes/settings.py`
Expected: No errors

**Step 5: Commit**

```bash
git add src/superseded/routes/settings.py
git commit -m "feat: add POST/DELETE endpoints for repo CRUD"
```

---

### Task 5: Add "Settings" link to nav bar

**Files:**
- Modify: `templates/base.html`

**Step 1: Add Settings link next to Metrics**

Change line ~132 in `base.html`:
```html
<div class="flex items-center gap-6">
    <a href="/settings" class="text-sm text-shell-400 hover:text-shell-200 transition-colors">Settings</a>
    <a href="/metrics" class="text-sm text-shell-400 hover:text-shell-200 transition-colors">Metrics</a>
    <span class="text-xs font-mono text-shell-500 bg-shell-900 px-2 py-1 rounded">Local-first pipeline</span>
</div>
```

**Step 2: Commit**

```bash
git add templates/base.html
git commit -m "feat: add Settings link to nav bar"
```

---

### Task 6: Add auto-clone to WorktreeManager

**Files:**
- Modify: `src/superseded/pipeline/worktree.py`

**Step 1: Store git_url per repo in registry**

Update `register_repo` to accept optional `git_url`:
```python
def register_repo(self, name: str, repo_path: str, git_url: str = "") -> None:
    self._repo_registry[name] = Path(repo_path)
    if git_url:
        self._git_urls[name] = git_url
```

Add `_git_urls` dict in `__init__`:
```python
self._git_urls: dict[str, str] = {}
```

**Step 2: Add `_ensure_repo_exists` method**

```python
async def _ensure_repo_exists(self, repo: str) -> None:
    repo_path = self._get_repo_path(repo)
    if repo_path.exists():
        return
    git_url = self._git_urls.get(repo, "")
    if not git_url:
        raise ValueError(
            f"Repo path {repo_path} does not exist and no git_url configured for '{repo}'"
        )
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    result = await self._run_git("clone", git_url, str(repo_path))
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to clone {git_url} to {repo_path}: {result.stderr}"
        )
```

**Step 3: Call `_ensure_repo_exists` in `create` method**

Add at the start of `create` (line ~58):
```python
if repo and repo != "primary":
    await self._ensure_repo_exists(repo)
```

**Step 4: Verify**

Run: `uv run ruff check src/superseded/pipeline/worktree.py`
Expected: No errors

**Step 5: Commit**

```bash
git add src/superseded/pipeline/worktree.py
git commit -m "feat: auto-clone repos from git_url when path missing"
```

**Step 1: Update `_build_pipeline_state` in `main.py`**

Update the `register_repo` call to pass `git_url`:
```python
for name, entry in config.repos.items():
    worktree_manager.register_repo(name, entry.path, entry.git_url)
```

**Step 2: Commit**

```bash
git add src/superseded/main.py
git commit -m "fix: pass git_url to worktree manager on startup"
```

---

### Task 7: Run full test suite and lint

**Step 1: Lint all files**

Run: `uv run ruff check src/ tests/`
Expected: No errors

**Step 2: Format check**

Run: `uv run ruff format --check src/ tests/`
Expected: No changes needed

**Step 3: Run tests**

Run: `uv run pytest tests/ -v`
Expected: All passing

**Step 4: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix: lint and format fixes for repo settings feature"
```
