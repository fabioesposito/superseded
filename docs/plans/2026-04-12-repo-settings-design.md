# Repo Settings Page — Design

## Overview

Add a `/settings` web page for managing repository configurations. Users can name repos, set git URLs, local paths, and branches. Repos are persisted in `.superseded/config.yaml`. When the pipeline needs a repo whose local path doesn't exist, it auto-clones from the configured `git_url`.

## Data Model

Add `git_url` to `RepoEntry` in `src/superseded/config.py`:

```python
class RepoEntry(BaseModel):
    path: str
    git_url: str = ""
    branch: str = ""
```

## Config Write-Back

Add `save_config(config, repo_path)` to `config.py` that serializes `SupersededConfig` to `.superseded/config.yaml`.

## Routes

New file `src/superseded/routes/settings.py`:

- `GET /settings` — render settings page with repo list from config
- `POST /settings/repos` — add a new repo (HTMX form, rewrites config.yaml, returns updated repo table)
- `PUT /settings/repos/{name}` — update an existing repo
- `DELETE /settings/repos/{name}` — remove a repo

## Template

`templates/settings.html` — matches existing dark theme design (shell/sand/neon palette, card layout, Outfit font).

- Table of repos: name, git_url, path, branch
- Inline add/edit form using Alpine.js for toggling
- HTMX-powered CRUD (hx-post, hx-put, hx-delete, hx-target=repo table)

## Nav Update

Add "Settings" link in `base.html` nav bar next to "Metrics".

## Auto-Clone

In `pipeline/worktree.py`, before operating on a repo:
1. Check if `path` exists on disk
2. If not and `git_url` is set, run `git clone <url> <path>`
3. If branch is set, checkout that branch after clone

## Config Reload

After writing config.yaml, rebuild `app.state.pipeline` so new repos take effect without restart.
