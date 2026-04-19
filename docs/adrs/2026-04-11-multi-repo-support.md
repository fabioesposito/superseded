---
title: Multi-Repo Support Implementation Plan
category: adrs
summary: Multi-Repo Support Implementation Plan
tags: []
date: 2026-04-11
---

# Multi-Repo Support Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable tickets that span multiple repositories (e.g., a frontend + backend change) so a single issue can drive work across repos.

**Architecture:** Extend the existing single-repo design with a `repos` map in config and a `repos` field in ticket frontmatter. A ticket lists which repos it targets. The pipeline runs SPEC/PLAN against the primary repo (where `.superseded/` lives), then fans out BUILD/VERIFY/REVIEW across each target repo in isolated worktrees. SHIP creates one PR per repo. Stage results become per-repo. Single-repo tickets (the default) continue to work unchanged.

**Tech Stack:** Python 3.12+, Pydantic, aiosqlite, FastAPI, Jinja2, git worktrees

---

## Design Decisions

1. **Backward compatible.** If a ticket has no `repos` field, behavior is identical to today (single repo).
2. **Config-driven repos.** `.superseded/config.yaml` gains a `repos` map naming available repos with their local paths. The top-level `repo_path` remains the primary (superseded host) repo.
3. **Ticket frontmatter.** New `repos` list field: `repos: [frontend, backend]`. These keys look up into `config.repos`.
4. **Fan-out at BUILD.** SPEC and PLAN stages run once (against primary repo). BUILD/VERIFY/REVIEW run once per target repo. SHIP creates a PR per repo.
5. **Per-repo artifacts.** Artifacts stored under `.superseded/artifacts/{issue_id}/{repo_name}/`.
6. **Per-repo stage results.** `stage_results` table gains a `repo` column. UI shows per-repo progress.

---

## Config Schema Change

### Example `.superseded/config.yaml`

```yaml
repo_path: /home/user/my-project          # primary (host) repo
repos:                                     # named repos this instance manages
  frontend:
    path: /home/user/my-frontend
  backend:
    path: /home/user/my-backend
  # primary repo is always implicitly available as "primary"
```

---

### Task 1: Extend `SupersededConfig` with `repos` map

**Files:**
- Modify: `src/superseded/config.py:9-21`

**Step 1: Write the failing test**

```python
# tests/test_config.py (add to existing file)
def test_config_repos_map():
    from superseded.config import SupersededConfig
    config = SupersededConfig(
        repo_path="/tmp/primary",
        repos={
            "frontend": {"path": "/tmp/frontend"},
            "backend": {"path": "/tmp/backend"},
        },
    )
    assert config.repos["frontend"]["path"] == "/tmp/frontend"
    assert config.repos["backend"]["path"] == "/tmp/backend"

def test_config_repos_empty_by_default():
    from superseded.config import SupersededConfig
    config = SupersededConfig(repo_path="/tmp/primary")
    assert config.repos == {}
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_config_repos_map -v`
Expected: FAIL — `repos` field does not exist on `SupersededConfig`

**Step 3: Add `repos` field to config model**

```python
# src/superseded/config.py
class RepoEntry(BaseModel):
    path: str
    branch: str = ""

class SupersededConfig(BaseModel):
    default_agent: str = "claude-code"
    stage_timeout_seconds: int = 600
    repo_path: str = ""
    repos: dict[str, RepoEntry] = Field(default_factory=dict)
    port: int = 8000
    host: str = "127.0.0.1"
    db_path: str = ".superseded/state.db"
    issues_dir: str = ".superseded/issues"
    artifacts_dir: str = ".superseded/artifacts"
    max_retries: int = 3
    retryable_stages: list[str] = Field(
        default_factory=lambda: ["build", "verify", "review"]
    )
```

Also update `load_config` to parse nested `repos` entries into `RepoEntry` objects.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/superseded/config.py tests/test_config.py
git commit -m "feat(config): add repos map to SupersededConfig"
```

---

### Task 2: Add `repos` field to `Issue` model and ticket frontmatter

**Files:**
- Modify: `src/superseded/models.py:39-61`
- Modify: `src/superseded/tickets/reader.py:10-22`
- Modify: `src/superseded/tickets/writer.py`

**Step 1: Write the failing test**

```python
# tests/test_models.py (add)
def test_issue_with_repos():
    from superseded.models import Issue
    issue = Issue(id="SUP-001", title="Test", repos=["frontend", "backend"])
    assert issue.repos == ["frontend", "backend"]

def test_issue_repos_default_empty():
    from superseded.models import Issue
    issue = Issue(id="SUP-001", title="Test")
    assert issue.repos == []

def test_issue_from_frontmatter_with_repos():
    from superseded.models import Issue
    content = """---
id: SUP-001
title: Cross-repo feature
repos:
  - frontend
  - backend
---

Body text
"""
    issue = Issue.from_frontmatter(content, filepath="/tmp/test.md")
    assert issue.repos == ["frontend", "backend"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py::test_issue_with_repos -v`
Expected: FAIL — `repos` field missing

**Step 3: Add `repos` field to `Issue`**

```python
# src/superseded/models.py
class Issue(BaseModel):
    id: str
    title: str
    status: IssueStatus = IssueStatus.NEW
    stage: Stage = Stage.SPEC
    created: datetime.date = Field(default_factory=datetime.date.today)
    assignee: str = ""
    labels: list[str] = Field(default_factory=list)
    filepath: str = ""
    repos: list[str] = Field(default_factory=list)
```

Update `from_frontmatter` to parse `repos`:
```python
    @classmethod
    def from_frontmatter(cls, content: str, filepath: str = "") -> "Issue":
        post = frontmatter.loads(content)
        return cls(
            id=post.get("id", "SUP-000"),
            title=post.get("title", "Untitled"),
            status=IssueStatus(post.get("status", "new")),
            stage=Stage(post.get("stage", "spec")),
            created=post.get("created", datetime.date.today()),
            assignee=post.get("assignee", ""),
            labels=post.get("labels", []),
            filepath=filepath,
            repos=post.get("repos", []),
        )
```

Update `tickets/reader.py` `read_issue` similarly to include `repos`.

**Step 4: Run tests**

Run: `uv run pytest tests/test_models.py tests/test_tickets.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/superseded/models.py src/superseded/tickets/reader.py tests/test_models.py
git commit -m "feat(models): add repos field to Issue for multi-repo tickets"
```

---

### Task 3: Add `repo` column to `stage_results` table

**Files:**
- Modify: `src/superseded/db.py:29-86` (schema), `src/superseded/db.py:153-184` (queries)

**Step 1: Write the failing test**

```python
# tests/test_db.py (add)
async def test_save_stage_result_with_repo(db):
    """stage_results supports an optional repo column."""
    from superseded.models import StageResult, Stage
    import datetime
    result = StageResult(
        stage=Stage.BUILD,
        passed=True,
        output="ok",
        started_at=datetime.datetime.now(),
        finished_at=datetime.datetime.now(),
    )
    await db.save_stage_result("SUP-001", result, repo="frontend")
    results = await db.get_stage_results("SUP-001")
    assert len(results) == 1
    assert results[0].get("repo") == "frontend"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_db.py::test_save_stage_result_with_repo -v`
Expected: FAIL — `repo` column doesn't exist

**Step 3: Add `repo` column to schema and update queries**

Add to `stage_results` schema:
```sql
CREATE TABLE IF NOT EXISTS stage_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id TEXT NOT NULL,
    repo TEXT DEFAULT 'primary',
    stage TEXT NOT NULL,
    passed INTEGER NOT NULL,
    output TEXT DEFAULT '',
    error TEXT DEFAULT '',
    artifacts TEXT DEFAULT '[]',
    started_at TEXT,
    finished_at TEXT,
    FOREIGN KEY (issue_id) REFERENCES issues(id)
);
```

Update `save_stage_result` to accept optional `repo` parameter (default `"primary"`):
```python
async def save_stage_result(self, issue_id: str, result: StageResult, repo: str = "primary") -> None:
    # ... INSERT with repo column
```

Update `get_stage_results` to optionally filter by repo:
```python
async def get_stage_results(self, issue_id: str, repo: str | None = None) -> list[dict[str, Any]]:
    # ... WHERE clause includes repo if provided
```

Use a migration approach: `ALTER TABLE stage_results ADD COLUMN repo TEXT DEFAULT 'primary'` in `initialize()` with `IF NOT EXISTS`-style handling (catch OperationalError or check pragma table_info).

**Step 4: Run tests**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/superseded/db.py tests/test_db.py
git commit -m "feat(db): add repo column to stage_results for multi-repo tracking"
```

---

### Task 4: Generalize `WorktreeManager` for multi-repo

**Files:**
- Modify: `src/superseded/pipeline/worktree.py`

**Step 1: Write the failing test**

```python
# tests/test_worktree.py (add)
async def test_worktree_manager_multi_repo(tmp_path):
    """WorktreeManager can create worktrees keyed by (issue_id, repo_name)."""
    from superseded.pipeline.worktree import WorktreeManager
    # Create two bare repos
    for name in ["primary", "frontend"]:
        repo = tmp_path / name
        repo.mkdir()
        await _init_bare_git_repo(repo)

    manager = WorktreeManager(str(tmp_path / "primary"))
    manager.register_repo("frontend", str(tmp_path / "frontend"))

    path = await manager.create("SUP-001", repo="frontend")
    assert path.exists()

    # primary repo worktree is separate
    primary_path = await manager.create("SUP-001")
    assert primary_path != path
    assert primary_path.exists()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_worktree.py::test_worktree_manager_multi_repo -v`
Expected: FAIL — `register_repo` and `repo` param don't exist

**Step 3: Extend `WorktreeManager`**

```python
# src/superseded/pipeline/worktree.py
class WorktreeManager:
    def __init__(self, repo_path: str) -> None:
        self.repo_path = Path(repo_path)
        self._worktrees_dir = self.repo_path / ".superseded" / "worktrees"
        self._repo_registry: dict[str, Path] = {}  # name -> repo_path

    def register_repo(self, name: str, repo_path: str) -> None:
        self._repo_registry[name] = Path(repo_path)

    def _get_repo_path(self, repo: str | None = None) -> Path:
        if repo and repo != "primary":
            if repo not in self._repo_registry:
                raise ValueError(f"Unknown repo: {repo}. Registered: {list(self._repo_registry.keys())}")
            return self._repo_registry[repo]
        return self.repo_path

    def _worktree_path(self, issue_id: str, repo: str | None = None) -> Path:
        if repo and repo != "primary":
            return self._worktrees_dir / f"{issue_id}__{repo}"
        return self._worktrees_dir / issue_id

    def _branch_name(self, issue_id: str, repo: str | None = None) -> str:
        if repo and repo != "primary":
            return f"issue/{issue_id}/{repo}"
        return f"issue/{issue_id}"

    async def _run_git(self, *args: str, cwd: str | None = None) -> ...:
        # same as before but uses cwd

    async def create(self, issue_id: str, repo: str | None = None) -> Path:
        repo_path = self._get_repo_path(repo)
        worktree_path = self._worktree_path(issue_id, repo)
        branch_name = self._branch_name(issue_id, repo)
        result = await self._run_git(
            "worktree", "add", str(worktree_path), "-b", branch_name,
            cwd=str(repo_path),
        )
        if result.returncode != 0:
            branch_result = await self._run_git(
                "worktree", "add", str(worktree_path), branch_name,
                cwd=str(repo_path),
            )
            if branch_result.returncode != 0:
                raise RuntimeError(...)
        return worktree_path

    async def cleanup(self, issue_id: str, repo: str | None = None) -> None:
        repo_path = self._get_repo_path(repo)
        worktree_path = self._worktree_path(issue_id, repo)
        branch_name = self._branch_name(issue_id, repo)
        if worktree_path.exists():
            await self._run_git("worktree", "remove", str(worktree_path), "--force", cwd=str(repo_path))
        await self._run_git("branch", "-D", branch_name, cwd=str(repo_path))

    def get_path(self, issue_id: str, repo: str | None = None) -> Path:
        return self._worktree_path(issue_id, repo)

    def exists(self, issue_id: str, repo: str | None = None) -> bool:
        return self._worktree_path(issue_id, repo).exists()
```

Key change: all methods accept an optional `repo` parameter. When `None` or `"primary"`, uses the primary repo path. When a named repo, looks it up in `_repo_registry` and uses that repo's path. Worktree paths include the repo name for disambiguation (`SUP-001__frontend`).

**Step 4: Run tests**

Run: `uv run pytest tests/test_worktree.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/superseded/pipeline/worktree.py tests/test_worktree.py
git commit -m "feat(worktree): support multi-repo worktree creation"
```

---

### Task 5: Update `ContextAssembler` for multi-repo context

**Files:**
- Modify: `src/superseded/pipeline/context.py`

**Step 1: Write the failing test**

```python
# tests/test_context.py (add)
def test_context_assembler_multi_repo(tmp_path):
    """ContextAssembler can assemble context from multiple repos."""
    from superseded.pipeline.context import ContextAssembler

    # Set up primary repo with AGENTS.md
    primary = tmp_path / "primary"
    primary.mkdir()
    (primary / "AGENTS.md").write_text("# Primary guide")

    # Set up frontend repo with its own AGENTS.md
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "AGENTS.md").write_text("# Frontend guide")

    assembler = ContextAssembler(str(primary))
    assembler.register_repo("frontend", str(frontend))

    from superseded.models import Issue
    issue = Issue(id="SUP-001", title="Test", repos=["frontend"])

    # When building context for a specific repo, include that repo's docs
    context = assembler.build(
        stage=Stage.BUILD,
        issue=issue,
        artifacts_path=str(tmp_path / "artifacts"),
        target_repo="frontend",
    )
    assert "Frontend guide" in context
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context.py::test_context_assembler_multi_repo -v`
Expected: FAIL — `register_repo` and `target_repo` don't exist

**Step 3: Extend `ContextAssembler`**

```python
# src/superseded/pipeline/context.py
class ContextAssembler:
    def __init__(self, repo_path: str) -> None:
        self.repo_path = Path(repo_path)
        self._repo_registry: dict[str, Path] = {}

    def register_repo(self, name: str, repo_path: str) -> None:
        self._repo_registry[name] = Path(repo_path)

    def _get_repo_path(self, repo: str | None = None) -> Path:
        if repo and repo in self._repo_registry:
            return self._repo_registry[repo]
        return self.repo_path

    def _build_agents_md_layer(self, repo: str | None = None) -> str | None:
        repo_path = self._get_repo_path(repo)
        content = self._read_if_exists(repo_path / "AGENTS.md")
        if content:
            label = f"{repo} repo" if repo else "Repository"
            return f"## {label} Guide (AGENTS.md)\n\n{content}"
        return None

    # Similar for _build_docs_index_layer, _build_rules_layer
    # Accept optional `repo` parameter, use _get_repo_path(repo)

    def build(
        self,
        stage: Stage,
        issue: Issue,
        artifacts_path: str,
        previous_errors: list[str] | None = None,
        iteration: int = 0,
        db: Any = None,
        target_repo: str | None = None,
    ) -> str:
        layers: list[str] = []

        # Primary repo context
        agents_md = self._build_agents_md_layer()
        if agents_md:
            layers.append(agents_md)

        # Target repo context (if different from primary)
        if target_repo and target_repo != "primary":
            target_agents_md = self._build_agents_md_layer(target_repo)
            if target_agents_md:
                layers.append(target_agents_md)
            target_docs = self._build_docs_index_layer(target_repo)
            if target_docs:
                layers.append(target_docs)

        # ... rest of layers same as before
```

When `target_repo` is set, the assembler includes AGENTS.md/docs/rules from both the primary repo and the target repo. This gives the agent full context for cross-repo work.

**Step 4: Run tests**

Run: `uv run pytest tests/test_context.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/superseded/pipeline/context.py tests/test_context.py
git commit -m "feat(context): support multi-repo context assembly"
```

---

### Task 6: Update `HarnessRunner` to fan out across repos

**Files:**
- Modify: `src/superseded/pipeline/harness.py:41-103` (`run_stage_with_retries`)
- Modify: `src/superseded/pipeline/harness.py:105-205` (`run_stage_streaming`)

**Step 1: Write the failing test**

```python
# tests/test_harness.py (add)
async def test_harness_fans_out_across_repos():
    """HarnessRunner runs BUILD once per target repo when issue.repos is set."""
    from superseded.pipeline.harness import HarnessRunner
    from superseded.models import Issue, Stage
    # ... mock agent, repos, etc.
    # Verify that with repos=["frontend","backend"], BUILD runs twice
```

**Step 2: Run test to verify it fails**

**Step 3: Extend `HarnessRunner`**

Add a new method that orchestrates multi-repo execution:

```python
# src/superseded/pipeline/harness.py
class HarnessRunner:
    # ... existing __init__ ...

    def _configure_repos(self, repos: dict[str, RepoEntry]) -> None:
        """Register named repos with worktree manager and context assembler."""
        for name, entry in repos.items():
            self.worktree_manager.register_repo(name, entry.path)
            self.context_assembler.register_repo(name, entry.path)

    async def run_stage_multi_repo(
        self,
        issue: Issue,
        stage: Stage,
        artifacts_path: str,
        db: Any = None,
        previous_errors: list[str] | None = None,
    ) -> dict[str, StageResult]:
        """Run a stage once per target repo. Returns {repo_name: StageResult}."""
        if not issue.repos:
            # Single-repo: fall back to existing behavior
            result = await self.run_stage_with_retries(
                issue, stage, artifacts_path, previous_errors
            )
            return {"primary": result}

        results: dict[str, StageResult] = {}
        for repo_name in issue.repos:
            repo_artifacts = str(Path(artifacts_path) / repo_name)
            Path(repo_artifacts).mkdir(parents=True, exist_ok=True)

            prompt = self.context_assembler.build(
                stage=stage,
                issue=issue,
                artifacts_path=repo_artifacts,
                previous_errors=previous_errors,
                target_repo=repo_name,
            )

            context = AgentContext(
                repo_path=self.repo_path,
                issue=issue,
                skill_prompt=prompt,
                artifacts_path=repo_artifacts,
            )
            context.worktree_path = str(
                self.worktree_manager.get_path(issue.id, repo=repo_name)
            )

            result = await self.run_stage_with_retries(
                issue, stage, repo_artifacts, previous_errors
            )
            results[repo_name] = result

        return results
```

The key insight: for each repo in `issue.repos`, the runner creates a separate worktree, assembles context targeting that repo, and runs the agent. Results are keyed by repo name.

**Step 4: Run tests**

Run: `uv run pytest tests/test_harness.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/superseded/pipeline/harness.py tests/test_harness.py
git commit -m "feat(harness): add run_stage_multi_repo for fan-out across repos"
```

---

### Task 7: Update pipeline route to handle multi-repo stages

**Files:**
- Modify: `src/superseded/routes/pipeline.py:55-122` (`_run_stage`)

**Step 1: Write the failing test**

```python
# tests/test_routes.py (add)
async def test_advance_multi_repo_issue(client, tmp_path):
    """POST /pipeline/issues/{id}/advance fans out across repos."""
    # Create a ticket with repos: [frontend, backend]
    # Verify two worktrees created
    # Verify two stage_results saved, one per repo
```

**Step 2: Run test to verify it fails**

**Step 3: Update `_run_stage` to handle multi-repo**

```python
# src/superseded/routes/pipeline.py
async def _run_stage(deps: Deps, issue_id: str, stage: Stage) -> StageResult:
    issues_dir = str(Path(deps.config.repo_path) / deps.config.issues_dir)
    issues = [i for i in list_issues(issues_dir) if i.id == issue_id]
    if not issues:
        return StageResult(stage=stage, passed=False, error="Issue not found")

    issue = issues[0]
    runner = _get_harness_runner(deps)

    # Register repos from config
    if deps.config.repos:
        runner._configure_repos(deps.config.repos)

    artifacts_path = str(
        Path(deps.config.repo_path) / deps.config.artifacts_dir / issue_id
    )
    Path(artifacts_path).mkdir(parents=True, exist_ok=True)

    worktree_manager = WorktreeManager(deps.config.repo_path)
    if deps.config.repos:
        for name, entry in deps.config.repos.items():
            worktree_manager.register_repo(name, entry.path)

    needs_worktree = stage in (Stage.BUILD, Stage.VERIFY, Stage.REVIEW)

    # Determine target repos
    target_repos = issue.repos if issue.repos else [None]  # None = primary

    all_passed = True
    combined_output = []

    for repo_name in target_repos:
        stash_ref = None
        worktree_created = False
        effective_repo = repo_name or "primary"

        if needs_worktree and not worktree_manager.exists(issue_id, repo=repo_name):
            stash_ref = await worktree_manager.stash_if_dirty()
            await worktree_manager.create(issue_id, repo=repo_name)
            worktree_created = True

        previous_errors: list[str] = []
        stage_results = await deps.db.get_stage_results(issue_id, repo=effective_repo)
        for sr in stage_results:
            if not sr.get("passed") and sr.get("error"):
                previous_errors.append(sr["error"])

        repo_artifacts = str(Path(artifacts_path) / effective_repo)
        Path(repo_artifacts).mkdir(parents=True, exist_ok=True)

        result = await runner.run_stage_with_retries(
            issue=issue,
            stage=stage,
            artifacts_path=repo_artifacts,
            previous_errors=previous_errors if previous_errors else None,
        )

        await deps.db.save_stage_result(issue_id, result, repo=effective_repo)

        if not result.passed:
            all_passed = False
            if stash_ref:
                await worktree_manager.pop_stash(stash_ref)

        combined_output.append(f"[{effective_repo}] {result.output or result.error}")

    # Aggregate result
    aggregate = StageResult(
        stage=stage,
        passed=all_passed,
        output="\n".join(combined_output),
        error="" if all_passed else "One or more repos failed",
    )

    # Update status based on aggregate
    if all_passed:
        await deps.db.update_issue_status(issue_id, IssueStatus.IN_PROGRESS, stage)
        update_issue_status(issue.filepath, IssueStatus.IN_PROGRESS, stage)
    else:
        await deps.db.update_issue_status(issue_id, IssueStatus.PAUSED, stage)
        update_issue_status(issue.filepath, IssueStatus.PAUSED, stage)

    return aggregate
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_routes.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/superseded/routes/pipeline.py tests/test_routes.py
git commit -m "feat(pipeline): fan out BUILD/VERIFY/REVIEW across repos"
```

---

### Task 8: Update SHIP stage for multi-repo PRs

**Files:**
- Modify: `src/superseded/pipeline/prompts.py:155-181` (SHIP prompt)
- Modify: `src/superseded/routes/pipeline.py` (SHIP handling in `_run_stage`)

**Step 1: Write the failing test**

```python
# tests/test_pipeline.py (add)
async def test_ship_creates_pr_per_repo():
    """SH stage with multiple repos creates a PR in each repo."""
    # Mock gh pr create calls
    # Verify one gh pr create per repo
```

**Step 2: Run test to verify it fails**

**Step 3: Update SHIP stage handling**

For SHIP stage with multi-repo, override the prompt to include repo-specific instructions:

```python
# In _run_stage, when stage == Stage.SHIP and issue.repos:
if stage == Stage.SHIP and issue.repos:
    for repo_name in issue.repos:
        # Create worktree if needed
        # Run agent in that repo's worktree
        # Agent creates commit, pushes, runs gh pr create in that repo
        pass
```

The SHIP prompt already instructs agents to use `gh pr create`. Since the agent runs in the repo's worktree, `gh` commands naturally target that repo's remote.

For the prompt injection, update `_build_skill_layer` in context.py to add repo-specific instructions:
```
## Target Repository: {repo_name}
You are working in the {repo_name} repository at {repo_path}.
Commit, push, and create a PR in THIS repository.
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/superseded/routes/pipeline.py src/superseded/pipeline/context.py tests/test_pipeline.py
git commit -m "feat(pipeline): create PR per repo in SHIP stage"
```

---

### Task 9: Update issue creation UI to support repos field

**Files:**
- Modify: `templates/issue_new.html`
- Modify: `src/superseded/routes/issues.py:27-67`

**Step 1: Write the failing test**

```python
# Playwright test or template test
def test_new_issue_form_has_repos_field():
    """The new issue form includes a repos multi-select or text input."""
```

**Step 2: Add repos input to the form**

Add a text input (comma-separated) or multi-select to `issue_new.html`:
```html
<div>
  <label for="repos">Target Repositories (comma-separated, leave empty for primary only)</label>
  <input type="text" id="repos" name="repos" placeholder="frontend, backend">
</div>
```

Update `create_issue` route to parse repos:
```python
repos_str = str(form.get("repos", "")).strip()
repos = (
    [r.strip() for r in repos_str.split(",") if r.strip()] if repos_str else []
)
```

Write repos to frontmatter:
```yaml
repos:
  - frontend
  - backend
```

**Step 3: Verify the form renders**

**Step 4: Commit**

```bash
git add templates/issue_new.html src/superseded/routes/issues.py
git commit -m "feat(ui): add repos field to issue creation form"
```

---

### Task 10: Update issue detail UI to show per-repo progress

**Files:**
- Modify: `templates/issue_detail.html`
- Modify: `src/superseded/routes/issues.py:70-101`

**Step 1: Update issue detail to show per-repo stage results**

In `issue_detail` route, group stage results by repo:
```python
results_by_repo: dict[str, list] = {}
for r in stage_results:
    repo = r.get("repo", "primary")
    results_by_repo.setdefault(repo, []).append(r)
```

Pass `results_by_repo` to the template.

In `issue_detail.html`, when the issue has multiple repos, render a tabbed or sectioned view:
```
## Repositories

### primary
[stage pipeline for primary]

### frontend
[stage pipeline for frontend]

### backend
[stage pipeline for backend]
```

**Step 2: Verify UI renders correctly**

**Step 3: Commit**

```bash
git add templates/issue_detail.html src/superseded/routes/issues.py
git commit -m "feat(ui): show per-repo stage progress on issue detail"
```

---

### Task 11: End-to-end integration test

**Files:**
- Create: `tests/test_multi_repo_integration.py`

**Step 1: Write the integration test**

```python
async def test_multi_repo_full_pipeline(tmp_path):
    """A ticket targeting two repos runs BUILD in both, saves per-repo results."""
    # 1. Create two git repos (primary, frontend)
    # 2. Write a ticket with repos: [frontend]
    # 3. Run SPEC -> PLAN -> BUILD
    # 4. Verify BUILD created a worktree for frontend
    # 5. Verify stage_results has two entries: primary (spec, plan) and frontend (build)
    # 6. Verify issue status is IN_PROGRESS
```

**Step 2: Run test**

Run: `uv run pytest tests/test_multi_repo_integration.py -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_multi_repo_integration.py
git commit -m "test: add multi-repo integration test"
```

---

### Task 12: Update AGENTS.md and docs

**Files:**
- Modify: `AGENTS.md`
- Create: `docs/multi-repo.md`

**Step 1: Document multi-repo in AGENTS.md**

Add to the Architecture section:
```markdown
### Multi-Repo Support

Tickets can target multiple repositories by setting `repos: [name1, name2]` in frontmatter.
Available repos are defined in `.superseded/config.yaml` under the `repos` key.
SPEC/PLAN run once (primary repo). BUILD/VERIFY/REVIEW fan out per target repo.
SHIP creates a PR per repo.
```

**Step 2: Create `docs/multi-repo.md`** with full usage guide:
- Config format
- Ticket format
- How the pipeline fans out
- Artifact structure
- Troubleshooting

**Step 3: Commit**

```bash
git add AGENTS.md docs/multi-repo.md
git commit -m "docs: document multi-repo support"
```

---

## Summary of Changes

| Layer | File | Change |
|-------|------|--------|
| Config | `src/superseded/config.py` | Add `repos: dict[str, RepoEntry]` |
| Models | `src/superseded/models.py` | Add `repos: list[str]` to `Issue` |
| Tickets | `src/superseded/tickets/reader.py` | Parse `repos` from frontmatter |
| Database | `src/superseded/db.py` | Add `repo` column to `stage_results` |
| Worktree | `src/superseded/pipeline/worktree.py` | Multi-repo worktree management |
| Context | `src/superseded/pipeline/context.py` | Cross-repo context assembly |
| Harness | `src/superseded/pipeline/harness.py` | Fan-out `run_stage_multi_repo` |
| Routes | `src/superseded/routes/pipeline.py` | Multi-repo stage execution |
| UI | `templates/issue_new.html` | Repos input field |
| UI | `templates/issue_detail.html` | Per-repo progress display |
| Docs | `AGENTS.md`, `docs/multi-repo.md` | Documentation |

## Artifact Structure (after multi-repo)

```
.superseded/
  artifacts/
    SUP-001/
      spec.md              # single (primary repo)
      plan.md              # single (primary repo)
      frontend/
        build_output.md    # per-repo
      backend/
        build_output.md    # per-repo
  worktrees/
    SUP-001                # primary worktree
    SUP-001__frontend      # frontend worktree
    SUP-001__backend       # backend worktree
```
