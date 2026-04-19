---
title: GitHub Issue Import for Tickets
category: adrs
summary: GitHub Issue Import for Tickets
tags: []
date: 2026-04-12
---

# GitHub Issue Import for Tickets

## Problem

Creating tickets requires manually copying content from GitHub issues into the Superseded form. This is error-prone and tedious, especially for issues with long descriptions and active comment threads.

## Approach

HTMX-powered import: a URL input + button above the new issue form. Clicking "Import" calls `gh issue view` on the server, parses the result, and replaces the form fields with pre-filled content for user review before submit.

## Components

### 1. UI: Import bar (`templates/issue_new.html`)

Add a row above the Title field:
- Input field for GitHub issue URL (placeholder: `https://github.com/owner/repo/issues/123`)
- "Import" button with `hx-post="/issues/import" hx-target="#issue-form" hx-swap="outerHTML"`
- Loading indicator via `htmx-indicator` class

### 2. Backend: `POST /issues/import` (`src/superseded/routes/issues.py`)

New route that:
1. Receives `github_url` from form data
2. Validates URL matches `https://github.com/{owner}/{repo}/issues/{number}`
3. Runs `gh issue view {url} --json title,body,labels,assignee,comments` via `asyncio.create_subprocess_exec`
4. Parses JSON output
5. Builds description: issue body + comments appended as `---\n**@author** (date):\n\nbody`
6. Returns HTML partial (the full form with pre-filled values)

Field mapping:
- `title` → title input value
- `body` + comments → description textarea value
- `labels[].name` → labels input (comma-separated)
- `assignee.login` → assignee select (only if matches "claude-code" or "opencode")

Sets hidden `github_url` field to the source URL.

### 3. Frontmatter: `github_url` field

- Hidden input in the form stores the imported URL
- `POST /issues/new` writes `github_url` to YAML frontmatter when present
- Model does not need changing — `github_url` is optional metadata

### 4. Error handling

- Invalid URL format → inline error below import bar
- `gh` CLI not found → clear error message
- Issue not found / API rate limit → show `gh` stderr
- Timeout (>15s) → timeout error message

## Files changed

- `templates/issue_new.html` — add import bar, hidden github_url field
- `src/superseded/routes/issues.py` — add `POST /issues/import` route, update `create_issue` to save github_url
