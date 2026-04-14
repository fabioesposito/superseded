# Pipeline Reliability Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 7 gaps found during end-to-end testing: repo auto-clone, GITHUB_TOKEN support, pre-flight checks, artifact persistence, and SSE progress events.

**Architecture:** Four groups of changes across config, UI, agent subprocess env injection, executor pre-flight, harness artifact writing, and SSE event wiring.

**Tech Stack:** Python 3.12+, FastAPI, HTMX, Jinja2, asyncio subprocess

---

## Task 1: Add `github_token` to config

**Files:**
- Modify: `src/superseded/config.py`

**Step 1: Add field to SupersededConfig**

```python
class SupersededConfig(BaseModel):
    # ... existing fields ...
    github_token: str = ""
```

**Step 2: Load from env in `load_config()`**

```python
def load_config(repo_path: Path) -> SupersededConfig:
    # ... existing code ...
    env_token = os.environ.get("GITHUB_TOKEN", "")
    if env_token:
        overrides["github_token"] = env_token
    return SupersededConfig(**overrides)
```

**Step 3: Verify config loads**

Run: `uv run python -c "from superseded.config import SupersededConfig; c = SupersededConfig(); print(c.github_token)"`
Expected: empty string

**Step 4: Commit**

```bash
git add src/superseded/config.py
git commit -m "feat(config): add github_token field with env loading"
```

---

## Task 2: Add GITHUB_TOKEN settings endpoint

**Files:**
- Modify: `src/superseded/routes/settings.py`

**Step 1: Add POST /settings/token endpoint**

```python
@router.post("/settings/token", response_class=HTMLResponse)
async def update_token(request: Request, deps: Deps = Depends(get_deps)):
    form = await _get_form_data(request)
    token = str(form.get("github_token", "")).strip()
    config = deps.config
    config.github_token = token
    save_config(config, Path(config.repo_path))
    _reload_pipeline(request.app, config)
    return get_templates().TemplateResponse(
        request,
        "_token_field.html",
        {"github_token": token, "success": True},
    )
```

**Step 2: Create `_token_field.html` template**

Create `templates/_token_field.html`:

```html
<div id="token-config">
    {% if success %}
    <div class="mb-4 px-5 py-3 text-sm text-olive-400 bg-olive-900/20 rounded-lg border border-olive-800/30">
        GitHub token saved successfully.
    </div>
    {% endif %}
    <div class="card rounded-xl p-6">
        <form hx-post="/settings/token" hx-target="#token-config" hx-swap="outerHTML">
            <div class="mb-4">
                <label class="block text-xs font-semibold uppercase tracking-widest text-sand-500 mb-1.5">GitHub Token</label>
                <input type="password" name="github_token" value="{{ github_token | default('') }}"
                       class="w-full bg-shell-900 border border-shell-700 rounded-lg px-3 py-2 text-shell-200 text-sm focus:outline-none focus:border-neon-500 transition-colors"
                       placeholder="ghp_xxxxxxxxxxxx">
                <p class="text-shell-500 text-xs mt-1">Used for gh CLI operations (PR creation). Set via GITHUB_TOKEN env var or here.</p>
            </div>
            <button type="submit" class="btn-primary text-white px-4 py-2 rounded-lg text-sm font-semibold">
                Save Token
            </button>
        </form>
    </div>
</div>
```

**Step 3: Add to settings.html**

Insert between repos table and agents section:

```html
<div class="mt-10 mb-3">
    <h2 class="text-lg font-semibold text-shell-100">GitHub</h2>
    <p class="text-shell-500 text-sm mt-1">Authentication for PR creation</p>
</div>
{% include "_token_field.html" %}
```

**Step 4: Pass github_token to template in settings_page()**

```python
response = get_templates().TemplateResponse(
    request,
    "settings.html",
    {
        "repos": repos,
        "stage_agents": stage_agents,
        "github_token": deps.config.github_token,
    },
)
```

**Step 5: Commit**

```bash
git add src/superseded/routes/settings.py templates/settings.html templates/_token_field.html
git commit -m "feat(settings): add GitHub token field"
```

---

## Task 3: Inject GITHUB_TOKEN into agent subprocesses

**Files:**
- Modify: `src/superseded/agents/base.py`
- Modify: `src/superseded/agents/factory.py`
- Modify: `src/superseded/pipeline/harness.py`

**Step 1: Add `github_token` param to AgentFactory.create()**

```python
class AgentFactory:
    def __init__(self, ..., github_token: str = "") -> None:
        # ...
        self.github_token = github_token

    def create(self, cli: str | None = None, model: str | None = None) -> AgentAdapter:
        # ...
        if cli == "claude-code":
            return ClaudeCodeAdapter(model=model, timeout=self.timeout, github_token=self.github_token)
        # ... etc
```

**Step 2: Add `github_token` to SubprocessAgentAdapter**

```python
class SubprocessAgentAdapter(AgentAdapter, ABC):
    def __init__(self, timeout: int = 600, github_token: str = "") -> None:
        self.timeout = timeout
        self.github_token = github_token

    def _build_env(self) -> dict[str, str] | None:
        if not self.github_token:
            return None
        env = os.environ.copy()
        env["GITHUB_TOKEN"] = self.github_token
        return env
```

**Step 3: Use `_build_env()` in `run()` and `run_streaming()`**

```python
async def run(self, prompt: str, context: AgentContext) -> AgentResult:
    cmd = self._build_command(prompt)
    cwd = self._get_cwd(context)
    env = self._build_env()
    # ...
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        env=env,  # <-- add this
        # ...
    )
```

Same for `run_streaming()`.

**Step 4: Update all adapters to accept github_token**

```python
class OpenCodeAdapter(SubprocessAgentAdapter):
    def __init__(self, model: str = "", timeout: int = 600, github_token: str = "") -> None:
        super().__init__(timeout=timeout, github_token=github_token)
```

Same for `ClaudeCodeAdapter` and `CodexAdapter`.

**Step 5: Wire github_token from config to factory in main.py**

```python
def _build_pipeline_state(config: SupersededConfig) -> PipelineState:
    factory = AgentFactory(
        default_agent=config.default_agent,
        default_model=config.default_model,
        timeout=config.stage_timeout_seconds,
        github_token=config.github_token,  # <-- add this
    )
```

**Step 6: Commit**

```bash
git add src/superseded/agents/ src/superseded/main.py
git commit -m "feat(agents): inject GITHUB_TOKEN into subprocess env"
```

---

## Task 4: Add gh auth pre-flight check

**Files:**
- Modify: `src/superseded/pipeline/executor.py`

**Step 1: Add `_check_gh_auth()` method**

```python
async def _check_gh_auth(self, github_token: str) -> tuple[bool, str]:
    import asyncio
    env = os.environ.copy()
    if github_token:
        env["GITHUB_TOKEN"] = github_token
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", "auth", "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            return True, ""
        return False, stderr.decode("utf-8", errors="replace")
    except FileNotFoundError:
        return False, "gh CLI not installed"
```

**Step 2: Call pre-flight in `_run_single_repo()` before SHIP stage**

```python
async def _run_single_repo(self, issue, stage, artifacts_path, repo_name, needs_worktree):
    # ... existing code ...

    # Pre-flight for ship stage
    if stage == Stage.SHIP:
        from superseded.pipeline.executor import Stage
        ok, msg = await self._check_gh_auth(self.runner.agent_factory.github_token)
        if not ok:
            return StageResult(
                stage=stage,
                passed=False,
                output="",
                error=f"gh auth failed: {msg}",
            )

    # ... rest of method ...
```

**Step 3: Add import for os at top of file**

```python
import os
```

**Step 4: Commit**

```bash
git add src/superseded/pipeline/executor.py
git commit -m "feat(pipeline): add gh auth pre-flight check before ship stage"
```

---

## Task 5: Fix repo auto-clone order

**Files:**
- Modify: `src/superseded/pipeline/executor.py`

**Step 1: Reorder in `_run_single_repo()`**

```python
try:
    if needs_worktree and not self.worktree_manager.exists(issue.id, repo=repo_name):
        await self.worktree_manager._ensure_repo_exists(repo_name)  # <-- move here
        stash_ref = await self.worktree_manager.stash_if_dirty(repo=repo_name)
        await self.worktree_manager.create(issue.id, repo=repo_name)
        worktree_created = True
except Exception:
```

**Step 2: Pass github_token for private repo cloning**

Modify `WorktreeManager._ensure_repo_exists()` to accept and use token:

```python
async def _ensure_repo_exists(self, repo: str, github_token: str = "") -> None:
    repo_path = self._get_repo_path(repo)
    if repo_path.exists():
        return
    git_url = self._git_urls.get(repo, "")
    if not git_url:
        raise ValueError(...)
    repo_path.parent.mkdir(parents=True, exist_ok=True)

    clone_url = git_url
    if github_token and "github.com" in git_url:
        # Inject token: https://github.com/org/repo -> https://{token}@github.com/org/repo
        clone_url = git_url.replace("https://github.com/", f"https://{github_token}@github.com/")

    result = await self._run_git("clone", clone_url, str(repo_path))
    if result.returncode != 0:
        raise RuntimeError(...)
```

**Step 3: Pass token from executor**

```python
await self.worktree_manager._ensure_repo_exists(
    repo_name,
    github_token=self.runner.agent_factory.github_token
)
```

**Step 4: Commit**

```bash
git add src/superseded/pipeline/executor.py src/superseded/pipeline/worktree.py
git commit -m "fix(pipeline): ensure repo exists before stash, support token auth for clone"
```

---

## Task 6: Persist spec/plan artifacts to disk

**Files:**
- Modify: `src/superseded/pipeline/harness.py`

**Step 1: Add artifact writing in `run_stage_with_retries()`**

After the success return in the retry loop:

```python
if passed:
    # Persist artifact file for spec/plan
    if stage in (Stage.SPEC, Stage.PLAN):
        artifact_file = Path(artifacts_path) / f"{stage.value}.md"
        artifact_file.parent.mkdir(parents=True, exist_ok=True)
        artifact_file.write_text(agent_result.stdout, encoding="utf-8")
    error = ""
    return StageResult(...)
```

**Step 2: Add Path import at top**

```python
from pathlib import Path  # already imported
```

**Step 3: Verify artifacts are written**

Run a pipeline and check: `ls .superseded/artifacts/SUP-XXX/`
Expected: `spec.md`, `plan.md`

**Step 4: Commit**

```bash
git add src/superseded/pipeline/harness.py
git commit -m "fix(harness): persist spec/plan output to artifact files"
```

---

## Task 7: Wire SSE progress events

**Files:**
- Modify: `src/superseded/pipeline/harness.py`
- Modify: `src/superseded/pipeline/executor.py`

**Step 1: Pass event_manager to run_stage_with_retries()**

```python
async def run_stage_with_retries(
    self,
    issue: Issue,
    stage: Stage,
    artifacts_path: str,
    previous_errors: list[str] | None = None,
    repo: str | None = None,
    event_manager: PipelineEventManager | None = None,
) -> StageResult:
```

**Step 2: Emit events at key points**

```python
async def _emit(self, em, issue_id, stage, content):
    if em:
        event = AgentEvent(event_type="progress", content=content, stage=stage)
        await em.publish(issue_id, event)
```

In the retry loop:

```python
await self._emit(event_manager, issue.id, stage, f"Starting {stage.value} stage (attempt {attempt + 1})...")
agent_result = await self.resolve_agent(stage).run(prompt, context)
await self._emit(event_manager, issue.id, stage, f"{stage.value} stage {'passed' if passed else 'failed'}")
```

**Step 3: Wire event_manager through executor**

In `executor.py:_run_single_repo()`:

```python
result = await self.runner.run_stage_with_retries(
    issue=issue,
    stage=stage,
    artifacts_path=repo_artifacts,
    previous_errors=repo_previous_errors if repo_previous_errors else None,
    repo=repo_name,
    event_manager=self.runner.event_manager,
)
```

**Step 4: Commit**

```bash
git add src/superseded/pipeline/harness.py src/superseded/pipeline/executor.py
git commit -m "feat(pipeline): emit SSE progress events during stage execution"
```

---

## Task 8: End-to-end verification

**Step 1: Run all tests**

```bash
uv run pytest tests/ -v
```

Fix any failures.

**Step 2: Manual verification**

1. Start server: `uv run superseded --port 8090`
2. Add repo with `git_url` but no local clone → should auto-clone
3. Set GITHUB_TOKEN in settings
4. Create issue and run pipeline
5. Verify `spec.md`/`plan.md` exist in artifacts
6. Verify ship stage checks auth and succeeds/fails clearly

**Step 3: Run linter**

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

**Step 4: Final commit if needed**

```bash
git add -A
git commit -m "chore: lint and format"
```
