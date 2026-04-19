---
title: GitHub Issue Import Implementation Plan
category: adrs
summary: GitHub Issue Import Implementation Plan
tags: []
date: 2026-04-12
---

# GitHub Issue Import Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add ability to paste a GitHub issue URL on the "New Issue" form and auto-fill fields using `gh issue view`.

**Architecture:** HTMX-powered import bar above the form. A `POST /issues/import` endpoint runs `gh issue view --json` to fetch issue data, then returns an HTML partial with form fields pre-filled. User reviews and submits normally.

**Tech Stack:** FastAPI, HTMX, `gh` CLI (already a project dependency), `asyncio.subprocess`

---

### Task 1: Add `fetch_github_issue` helper function

**Files:**
- Create: `src/superseded/github.py`
- Test: `tests/test_github.py`

**Step 1: Write the failing tests**

```python
# tests/test_github.py
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from superseded.github import fetch_github_issue


@pytest.mark.asyncio
async def test_fetch_github_issue_parses_response():
    gh_output = json.dumps({
        "title": "Fix login bug",
        "body": "The login page crashes on Firefox.",
        "labels": [{"name": "bug"}, {"name": "priority-high"}],
        "assignee": {"login": "claude-code"},
        "comments": [
            {"author": {"login": "alice"}, "body": "I can reproduce this.", "createdAt": "2026-04-10T12:00:00Z"},
            {"author": {"login": "bob"}, "body": "Working on a fix.", "createdAt": "2026-04-11T09:00:00Z"},
        ],
    })

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (gh_output.encode(), b"")
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await fetch_github_issue("https://github.com/owner/repo/issues/42")

    assert result.title == "Fix login bug"
    assert result.body == "The login page crashes on Firefox."
    assert result.labels == ["bug", "priority-high"]
    assert result.assignee == "claude-code"
    assert len(result.comments) == 2
    assert result.comments[0].author == "alice"
    assert "I can reproduce this." in result.comments[0].body


@pytest.mark.asyncio
async def test_fetch_github_issue_handles_no_assignee():
    gh_output = json.dumps({
        "title": "Add dark mode",
        "body": "Feature request",
        "labels": [],
        "assignee": None,
        "comments": [],
    })

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (gh_output.encode(), b"")
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await fetch_github_issue("https://github.com/owner/repo/issues/1")

    assert result.assignee == ""
    assert result.labels == []


@pytest.mark.asyncio
async def test_fetch_github_issue_invalid_url():
    with pytest.raises(ValueError, match="Invalid GitHub issue URL"):
        await fetch_github_issue("https://not-github.com/foo")


@pytest.mark.asyncio
async def test_fetch_github_issue_gh_error():
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"gh: could not resolve to an Issue")
    mock_proc.returncode = 1

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with pytest.raises(RuntimeError, match="gh: could not resolve"):
            await fetch_github_issue("https://github.com/owner/repo/issues/999")
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_github.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'superseded.github'`

**Step 3: Write the implementation**

```python
# src/superseded/github.py
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field


@dataclass
class GhComment:
    author: str
    body: str
    created_at: str


@dataclass
class GhIssue:
    title: str
    body: str
    labels: list[str]
    assignee: str
    comments: list[GhComment]
    url: str


GITHUB_ISSUE_URL_RE = re.compile(
    r"^https://github\.com/([^/]+)/([^/]+)/issues/(\d+)"
)


def parse_github_url(url: str) -> tuple[str, str, int]:
    match = GITHUB_ISSUE_URL_RE.match(url.strip())
    if not match:
        raise ValueError(
            "Invalid GitHub issue URL. Expected: https://github.com/owner/repo/issues/123"
        )
    owner, repo, number = match.groups()
    return owner, repo, int(number)


async def fetch_github_issue(url: str) -> GhIssue:
    owner, repo, number = parse_github_url(url)

    proc = await asyncio.create_subprocess_exec(
        "gh",
        "issue",
        "view",
        str(number),
        "--repo",
        f"{owner}/{repo}",
        "--json",
        "title,body,labels,assignee,comments",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(stderr.decode().strip())

    data = json.loads(stdout)

    labels = [label["name"] for label in data.get("labels", [])]
    assignee = ""
    if data.get("assignee") and data["assignee"].get("login"):
        assignee = data["assignee"]["login"]

    comments = []
    for c in data.get("comments", []):
        author = c.get("author", {}).get("login", "unknown")
        comments.append(
            GhComment(
                author=author,
                body=c.get("body", ""),
                created_at=c.get("createdAt", ""),
            )
        )

    return GhIssue(
        title=data.get("title", ""),
        body=data.get("body", ""),
        labels=labels,
        assignee=assignee,
        comments=comments,
        url=url,
    )


def format_description(body: str, comments: list[GhComment]) -> str:
    parts = [body] if body else []
    for c in comments:
        date_str = c.created_at[:10] if c.created_at else ""
        parts.append(f"---\n**@{c.author}** ({date_str}):\n\n{c.body}")
    return "\n\n".join(parts)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_github.py -v`
Expected: 4 PASS

**Step 5: Lint and format**

Run: `uv run ruff check src/superseded/github.py tests/test_github.py && uv run ruff format src/superseded/github.py tests/test_github.py`

**Step 6: Commit**

```bash
git add src/superseded/github.py tests/test_github.py
git commit -m "feat: add GitHub issue fetcher helper"
```

---

### Task 2: Add `POST /issues/import` route

**Files:**
- Modify: `src/superseded/routes/issues.py:1-164`
- Test: `tests/test_issue_routes.py`

**Step 1: Write the failing test**

Add to `tests/test_issue_routes.py`:

```python
from unittest.mock import AsyncMock, patch

from superseded.github import GhComment, GhIssue


async def test_import_github_issue_returns_form_partial(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()

    mock_issue = GhIssue(
        title="Fix login bug",
        body="The login page crashes.",
        labels=["bug", "priority-high"],
        assignee="claude-code",
        comments=[
            GhComment(author="alice", body="Reproduced.", created_at="2026-04-10T12:00:00Z"),
        ],
        url="https://github.com/owner/repo/issues/42",
    )

    with patch("superseded.routes.issues.fetch_github_issue", return_value=mock_issue):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/issues/import",
                data={"github_url": "https://github.com/owner/repo/issues/42"},
            )

    assert resp.status_code == 200
    assert "Fix login bug" in resp.text
    assert "The login page crashes." in resp.text
    assert "bug, priority-high" in resp.text
    assert "claude-code" in resp.text
    assert "@alice" in resp.text
    assert "https://github.com/owner/repo/issues/42" in resp.text


async def test_import_github_issue_invalid_url(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/issues/import",
            data={"github_url": "https://not-github.com/foo"},
        )

    assert resp.status_code == 200
    assert "Invalid GitHub issue URL" in resp.text
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_issue_routes.py::test_import_github_issue_returns_form_partial tests/test_issue_routes.py::test_import_github_issue_invalid_url -v`
Expected: FAIL (404 — route doesn't exist)

**Step 3: Add the import route and import helper**

Add to `src/superseded/routes/issues.py` (after existing imports, before `router`):

```python
from superseded.github import fetch_github_issue, format_description
```

Add the new route (after the `new_issue_form` route, before `create_issue`):

```python
@router.post("/import", response_class=HTMLResponse)
async def import_github_issue(request: Request, deps: Deps = Depends(get_deps)):
    form = await request.form()
    github_url = str(form.get("github_url", "")).strip()

    try:
        gh_issue = await fetch_github_issue(github_url)
    except (ValueError, RuntimeError) as e:
        return get_templates().TemplateResponse(
            request,
            "issue_new.html",
            {"error": str(e)},
        )

    description = format_description(gh_issue.body, gh_issue.comments)
    labels_str = ", ".join(gh_issue.labels)

    return get_templates().TemplateResponse(
        request,
        "issue_new.html",
        {
            "title": gh_issue.title,
            "body": description,
            "labels": labels_str,
            "assignee": gh_issue.assignee,
            "github_url": gh_issue.url,
        },
    )
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_issue_routes.py::test_import_github_issue_returns_form_partial tests/test_issue_routes.py::test_import_github_issue_invalid_url -v`
Expected: 2 PASS

**Step 5: Lint**

Run: `uv run ruff check src/superseded/routes/issues.py tests/test_issue_routes.py`

**Step 6: Commit**

```bash
git add src/superseded/routes/issues.py tests/test_issue_routes.py
git commit -m "feat: add /issues/import endpoint for GitHub issue fetch"
```

---

### Task 3: Update the form template with import bar and field pre-filling

**Files:**
- Modify: `templates/issue_new.html`

**Step 1: Add the import bar and pre-fill support**

Replace `templates/issue_new.html` with:

```html
{% extends "base.html" %}
{% block title %}New Issue - Superseded{% endblock %}
{% block content %}
<div class="animate-fade-in max-w-2xl">
    <h1 class="text-3xl font-bold text-shell-50 tracking-tight mb-8">New Issue</h1>

    {% if error %}
    <div class="mb-6 p-4 bg-red-900/30 border border-red-700/50 rounded-lg text-red-300 text-sm">
        {{ error }}
    </div>
    {% endif %}

    <div class="mb-6 p-4 bg-shell-800/50 border border-shell-700/40 rounded-lg">
        <label class="block text-xs font-semibold uppercase tracking-widest text-sand-500 mb-2">Import from GitHub</label>
        <div class="flex gap-2">
            <input type="text" name="github_url" form="import-form"
                class="flex-1 bg-shell-900 border border-shell-700/60 rounded-lg px-4 py-3 text-shell-100 placeholder-shell-600 focus:outline-none focus:border-neon-600 focus:ring-1 focus:ring-neon-600/30 transition-all"
                placeholder="https://github.com/owner/repo/issues/123"
                value="{{ github_url or '' }}">
            <form id="import-form" hx-post="/issues/import" hx-target="#form-container" hx-swap="outerHTML" hx-indicator="#import-spinner">
                <button type="submit" class="btn-secondary text-shell-300 hover:text-shell-100 bg-shell-800 hover:bg-shell-700 border border-shell-700/60 px-5 py-3 rounded-lg text-sm font-semibold transition-colors whitespace-nowrap">
                    <span class="htmx-indicator" id="import-spinner">Loading...</span>
                    <span class="htmx-no-indicator">Import</span>
                </button>
            </form>
        </div>
        <p class="mt-1.5 text-xs text-shell-600">Paste a GitHub issue URL to auto-fill the fields below.</p>
    </div>

    <div id="form-container">
    <form action="/issues/new" method="post" class="space-y-5">
        <input type="hidden" name="github_url" value="{{ github_url or '' }}">
        <div>
            <label class="block text-xs font-semibold uppercase tracking-widest text-sand-500 mb-2">Title</label>
            <input type="text" name="title" required
                class="w-full bg-shell-900 border border-shell-700/60 rounded-lg px-4 py-3 text-shell-100 placeholder-shell-600 focus:outline-none focus:border-neon-600 focus:ring-1 focus:ring-neon-600/30 transition-all"
                placeholder="Brief description of the task"
                value="{{ title or '' }}">
        </div>
        <div>
            <label class="block text-xs font-semibold uppercase tracking-widest text-sand-500 mb-2">Description</label>
            <textarea name="body" rows="10"
                class="w-full bg-shell-900 border border-shell-700/60 rounded-lg px-4 py-3 text-shell-100 placeholder-shell-600 focus:outline-none focus:border-neon-600 focus:ring-1 focus:ring-neon-600/30 transition-all font-mono text-sm"
                placeholder="Detailed specification, requirements, acceptance criteria...">{{ body or '' }}</textarea>
        </div>
        <div>
            <label class="block text-xs font-semibold uppercase tracking-widest text-sand-500 mb-2">Labels</label>
            <input type="text" name="labels"
                class="w-full bg-shell-900 border border-shell-700/60 rounded-lg px-4 py-3 text-shell-100 placeholder-shell-600 focus:outline-none focus:border-neon-600 focus:ring-1 focus:ring-neon-600/30 transition-all"
                placeholder="bug, feature, enhancement"
                value="{{ labels or '' }}">
        </div>
        <div>
            <label class="block text-xs font-semibold uppercase tracking-widest text-sand-500 mb-2">Target Repositories</label>
            <input type="text" name="repos"
                class="w-full bg-shell-900 border border-shell-700/60 rounded-lg px-4 py-3 text-shell-100 placeholder-shell-600 focus:outline-none focus:border-neon-600 focus:ring-1 focus:ring-neon-600/30 transition-all"
                placeholder="frontend, backend (leave empty for primary only)">
            <p class="mt-1.5 text-xs text-shell-600">Comma-separated repo names from config. Leave empty for single-repo work.</p>
        </div>
        <div>
            <label class="block text-xs font-semibold uppercase tracking-widest text-sand-500 mb-2">Assign to agent</label>
            <select name="assignee"
                class="w-full bg-shell-900 border border-shell-700/60 rounded-lg px-4 py-3 text-shell-100 focus:outline-none focus:border-neon-600 focus:ring-1 focus:ring-neon-600/30 transition-all appearance-none cursor-pointer">
                <option value="" {% if not assignee %}selected{% endif %}>auto</option>
                <option value="claude-code" {% if assignee == 'claude-code' %}selected{% endif %}>claude-code</option>
                <option value="opencode" {% if assignee == 'opencode' %}selected{% endif %}>opencode</option>
            </select>
        </div>
        <div class="flex gap-3 pt-2">
            <button type="submit" class="btn-primary text-white px-6 py-2.5 rounded-lg text-sm font-semibold">Create Issue</button>
            <a href="/" class="btn-secondary text-shell-400 hover:text-shell-200 px-6 py-2.5 rounded-lg text-sm font-medium transition-colors">Cancel</a>
        </div>
    </form>
    </div>
</div>
{% endblock %}
```

**Step 2: Run existing tests to verify no regressions**

Run: `uv run pytest tests/test_issue_routes.py -v`
Expected: All existing tests still PASS

**Step 3: Lint**

Run: `uv run ruff check templates/` (if applicable) — templates aren't Python, so just verify existing route tests pass.

**Step 4: Commit**

```bash
git add templates/issue_new.html
git commit -m "feat: add GitHub import bar to new issue form"
```

---

### Task 4: Save `github_url` to frontmatter on issue creation

**Files:**
- Modify: `src/superseded/routes/issues.py` (the `create_issue` route)

**Step 1: Write the failing test**

Add to `tests/test_issue_routes.py`:

```python
async def test_create_issue_saves_github_url(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    await app.state.db.initialize()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/issues/new",
            data={
                "title": "Imported issue",
                "body": "From GitHub",
                "labels": "bug",
                "assignee": "",
                "repos": "",
                "github_url": "https://github.com/owner/repo/issues/42",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    issues_dir = Path(tmp_repo) / ".superseded" / "issues"
    md_files = list(issues_dir.glob("*.md"))
    assert len(md_files) == 1

    content = md_files[0].read_text()
    assert "github_url: https://github.com/owner/repo/issues/42" in content
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_issue_routes.py::test_create_issue_saves_github_url -v`
Expected: FAIL (github_url not in frontmatter)

**Step 3: Update `create_issue` to handle `github_url`**

In `src/superseded/routes/issues.py`, in the `create_issue` function, add after the existing form parsing:

```python
github_url = str(form.get("github_url", "")).strip()
```

And update the content template to include the github_url in frontmatter when present:

```python
github_url_line = f'github_url: "{github_url}"' if github_url else ""
content = f"""---
id: {issue_id}
title: {title}
status: new
stage: spec
created: "{date.today().isoformat()}"
assignee: {assignee}
labels:
{labels_yaml}
repos:
{repos_yaml}
{github_url_line}
---

{body}
"""
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_issue_routes.py::test_create_issue_saves_github_url -v`
Expected: PASS

**Step 5: Run all issue route tests**

Run: `uv run pytest tests/test_issue_routes.py -v`
Expected: All PASS

**Step 6: Lint**

Run: `uv run ruff check src/superseded/routes/issues.py tests/test_issue_routes.py`

**Step 7: Commit**

```bash
git add src/superseded/routes/issues.py tests/test_issue_routes.py
git commit -m "feat: save github_url to ticket frontmatter on import"
```

---

### Task 5: End-to-end verification

**Step 1: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS

**Step 2: Run lint and format**

Run: `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: Clean

**Step 3: Manual verification**

Run: `uv run superseded` and navigate to `/issues/new`. Verify:
- Import bar is visible
- Pasting a GitHub issue URL and clicking Import fills the form
- Submitting creates the ticket with github_url in frontmatter
