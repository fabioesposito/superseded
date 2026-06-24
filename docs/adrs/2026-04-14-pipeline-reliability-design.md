---
title: Pipeline Reliability Improvements — Design
category: adrs
summary: Pipeline Reliability Improvements — Design
tags: []
date: 2026-04-14
---

# Pipeline Reliability Improvements — Design

## Problem

End-to-end testing of Superseded revealed 7 gaps that prevent reliable unattended pipeline execution:

1. **Repo auto-clone fails** — `FileNotFoundError` when repo path doesn't exist locally
2. **CSRF friction** — Hard to use via curl/CLI for automation
3. **No progress feedback** — Ship stage took 2+ minutes with no indicator
4. **Artifacts not persisted** — `spec.md`/`plan.md` files never written to disk
5. **No pre-flight checks** — Ship stage silently fails without `gh` auth
6. **No GITHUB_TOKEN support** — No way to authenticate `gh` CLI from the app
7. **Port conflicts** — Unclear error when port is already in use

## Scope

Groups 1-4 implemented together:

| Group | Items | Complexity |
|-------|-------|------------|
| 1 | GITHUB_TOKEN + pre-flight | Medium |
| 2 | Repo auto-clone fix | Small |
| 3 | Artifact persistence | Small |
| 4 | SSE progress events | Medium |

## Design

### Group 1: GITHUB_TOKEN + Ship Stage Reliability

#### Config

Add `github_token: str = ""` to `SupersededConfig` in `config.py`. Load from `GITHUB_TOKEN` env var at startup (same pattern as `SUPERSEDED_API_KEY`).

#### Settings UI

Add a "GitHub Token" field in `settings.html`:
- Password-type input between Repos table and Pipeline Agents section
- Saves via new `POST /settings/token` endpoint
- Shows current token as masked `ghp_****abcd` if set

#### Subprocess env injection

In `agents/base.py`, modify `SubprocessAgentAdapter.run()` and `run_streaming()`:
- Build `env` dict from `os.environ` merged with `{"GITHUB_TOKEN": token}` from config
- Pass `env=env` to `asyncio.create_subprocess_exec()`
- `gh` CLI automatically uses `GITHUB_TOKEN` env var

#### Pre-flight check

In `pipeline/executor.py`, add `_check_gh_auth()`:
- Runs `gh auth status` as subprocess
- Returns `(success: bool, message: str)`
- Called in `_run_single_repo()` before SHIP stage
- If fails, return `StageResult(passed=False, error="gh auth failed: ...")`

### Group 2: Repo Auto-Clone

**Root cause:** `executor.py:79-82` calls `stash_if_dirty()` before `_ensure_repo_exists()`.

**Fix:** Reorder in `_run_single_repo()`:
```python
await self.worktree_manager._ensure_repo_exists(repo_name)
stash_ref = await self.worktree_manager.stash_if_dirty(repo=repo_name)
await self.worktree_manager.create(issue_id, repo=repo_name)
```

**Private repo auth:** Pass `github_token` to `_ensure_repo_exists()` and inject into clone URL as `https://{token}@github.com/org/repo.git`.

### Group 3: Artifact Persistence

In `harness.py:run_stage_with_retries()`, after successful SPEC or PLAN stage:

```python
if passed and stage in (Stage.SPEC, Stage.PLAN):
    artifact_file = Path(artifacts_path) / f"{stage.value}.md"
    artifact_file.parent.mkdir(parents=True, exist_ok=True)
    artifact_file.write_text(agent_result.stdout, encoding="utf-8")
```

`ContextAssembler._build_artifacts_layer()` already reads `*.md` from artifacts dir.

### Group 4: SSE Progress Events

Wire existing `PipelineEventManager` into `run_stage_with_retries()`:

1. Add `event_manager: PipelineEventManager | None = None` param
2. Emit `AgentEvent` at key points:
   - "Cloning repository..." (before `_ensure_repo_exists`)
   - "Agent starting..." (before agent run)
   - "Stage completed" (after success/failure)
3. SSE stream on issue detail page already subscribes via `EventSourceResponse`

## Files to Modify

| File | Changes |
|------|---------|
| `src/superseded/config.py` | Add `github_token` field, env loading |
| `src/superseded/routes/settings.py` | Add `POST /settings/token` endpoint |
| `templates/settings.html` | Add token input field |
| `src/superseded/agents/base.py` | Inject `GITHUB_TOKEN` into subprocess env |
| `src/superseded/pipeline/executor.py` | Pre-flight check, auto-clone reorder |
| `src/superseded/pipeline/harness.py` | Artifact persistence, SSE events |
| `src/superseded/pipeline/worktree.py` | Token-aware git clone |

## Verification

1. Add repo with `git_url` but no local path → should auto-clone
2. Run pipeline without `gh auth` → ship stage should fail with clear error
3. Set GITHUB_TOKEN in settings → ship stage should succeed
4. Check `.superseded/artifacts/{id}/` → should contain `spec.md` and `plan.md`
5. Watch issue detail page during pipeline → should see progress events via SSE
