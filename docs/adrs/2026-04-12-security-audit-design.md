# Security Audit Design

## Context

Superseded has grown several features (issue creation, GitHub import, multi-repo settings, agent execution) without security hardening. As a local-first tool that executes subprocess agents and manipulates git repos, the app has a significant attack surface even when bound to localhost. This audit addresses command injection, path traversal, XSS, authentication, CSRF, and Docker hardening.

## Vulnerability Analysis

### Critical: Command Injection

**Agents** (`src/superseded/agents/claude_code.py:7-8`, `src/superseded/agents/opencode.py:7-8`):
User-controlled issue content flows through `ContextAssembler` into prompts, which are passed as positional CLI arguments to `claude` and `opencode`. A malicious issue body containing shell metacharacters or prompt injection could hijack agent execution.

**Git URLs** (`src/superseded/routes/settings.py:37`, `src/superseded/pipeline/worktree.py:37`):
User-supplied `git_url` from the settings form is passed directly to `git clone`. An attacker could inject additional git arguments or shell commands via crafted URLs.

### Critical: Path Traversal

**Issue IDs** (`src/superseded/routes/issues.py:74`, `src/superseded/pipeline/worktree.py:50-53`):
The `issue_id` URL parameter is used to construct file paths (`issues_dir / f"{issue_id}-{slug}.md"`) and worktree paths (`worktrees_dir / issue_id`). While the slug generation uses regex, the issue_id itself is not validated. The `{issue_id}` in URL paths is used by FastAPI path converters, but direct file operations still need bounds.

### High: XSS

**Templates** (`templates/issue_detail.html:18,130,144`):
Jinja2 autoescaping is on by default with FastAPI's Jinja2Templates, but JavaScript in `issue_detail.html:172` interpolates `{{ issue.id }}` into a JS string. Agent output (`result.error`, `iter.error`) is rendered in template expressions. Need to verify autoescaping covers all cases.

### High: No Authentication

All endpoints are open. Anyone with network access to the server can create issues, trigger pipeline stages, modify repo settings, and execute arbitrary agents.

### High: No CSRF Protection

POST forms lack CSRF tokens. Cross-site requests from malicious pages could create issues, trigger pipelines, or modify settings.

### Medium: Unsafe Config Paths

User-provided `path` in settings is written to config and used for filesystem operations without validating it points to a legitimate git repo.

### Medium: Docker Runs as Root

The Dockerfile has no `USER` directive and exposes the app as root inside the container.

## Design: Fix Layers

### Layer 1: Input Validation

Create `src/superseded/validation.py` with validators for all user-controlled inputs:

- `validate_issue_id(value: str) -> str`: Ensure matches `SUP-\d{3,}` pattern
- `validate_git_url(value: str) -> str`: Allow only `https://`, `git@`, `ssh://` patterns; reject shell metacharacters
- `validate_repo_path(value: str) -> str`: Ensure path is absolute and within allowed directories
- `validate_agent_prompt(value: str) -> str`: Truncate to reasonable length, strip null bytes

Apply validators in route handlers before passing to downstream functions.

### Layer 2: Command Injection Hardening

**Agents**: Pass prompts via stdin instead of CLI arguments. Change `_build_command` to use `--input` flag or pipe to stdin. Use `subprocess` list form (already done) but ensure no shell interpolation.

**Git**: Validate git_url before passing to subprocess. Use `subprocess` list form (already done). Add `--` separator to prevent argument injection.

### Layer 3: XSS Fixes

- Verify Jinja2 autoescaping is enabled (it is by default with FastAPI)
- In `issue_detail.html`, the JS line `var issueId = '{{ issue.id }}'` is safe (issue_id is validated to be `SUP-\d+`)
- Ensure all `{{ error }}`, `{{ result.error }}`, `{{ iter.error }}` renderings are in HTML context (not JS/string context)
- Add `| e` filter explicitly where agent output is rendered in HTML

### Layer 4: Authentication

Add optional API key auth via config:

- Add `api_key: str = ""` to `SupersededConfig`
- Create middleware that checks `X-API-Key` header or `api_key` query param
- Skip auth for `/health` and static files
- When `api_key` is empty, auth is disabled (backward-compatible for local use)

### Layer 5: CSRF Protection

Add CSRF token middleware:

- Generate CSRF token per session (stored in cookie)
- Validate `X-CSRF-Token` header on POST/PUT/DELETE requests
- Include CSRF token in HTMX forms via meta tag
- Skip for API key-authenticated requests

### Layer 6: Docker Hardening

- Add non-root user in Dockerfile
- Set `USER` directive
- Add `--read-only` flag suggestion in docs
- Remove unnecessary packages

## Implementation Order

1. `src/superseded/validation.py` — new validation module
2. `src/superseded/routes/issues.py` — apply input validators
3. `src/superseded/routes/settings.py` — validate git_url and path
4. `src/superseded/routes/pipeline.py` — validate issue_id
5. `src/superseded/agents/claude_code.py` — pass prompt via stdin
6. `src/superseded/agents/opencode.py` — pass prompt via stdin
7. `src/superseded/agents/base.py` — update subprocess to use stdin
8. `templates/*.html` — verify/fix XSS
9. `src/superseded/main.py` — add auth + CSRF middleware
10. `src/superseded/config.py` — add api_key field
11. `Dockerfile` — add non-root user

## Verification

- Run `uv run ruff check src/ tests/` after each change
- Run `uv run pytest tests/ -v` to verify no regressions
- Manually test: create issue with shell metacharacters in title/body
- Manually test: attempt path traversal via issue_id like `../../etc/passwd`
- Manually test: settings form with malicious git_url
- Verify templates render agent output safely (no script execution)
