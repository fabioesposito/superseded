# HITL and Docker Sandbox Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement Human-in-the-Loop (HITL) approval checkpoints and Docker sandbox execution for agents.

**Architecture:** We will add a `DockerAgentAdapter` to execute agents inside a standard container, mounting the repository worktree. We will also add a new `PauseReason` for approvals, pausing the pipeline automatically if a stage requires approval or if an agent outputs an `approval.md` file. The web UI will handle the "Approve/Reject" flows.

**Tech Stack:** Python 3.12+, FastAPI, HTMX, Docker.

---

### Task 1: Update Models and Configuration

**Files:**
- Modify: `/home/debian/workspace/superseded/src/superseded/models.py`
- Modify: `/home/debian/workspace/superseded/src/superseded/config.py`

**Step 1: Write the failing tests (or modify models and verify type checking)**

Modify `models.py` to add `APPROVAL_REQUIRED` to `PauseReason`.
```python
class PauseReason(StrEnum):
    RETRIES_EXHAUSTED = "retries-exhausted"
    AWAITING_INPUT = "awaiting-input"
    USER_EDIT = "user-edit"
    APPROVAL_REQUIRED = "approval-required"
```

Modify `config.py` to add `sandbox` and `require_approval` to `StageAgentConfig`:
```python
class StageAgentConfig(BaseModel):
    cli: str = "claude-code"
    model: str = ""
    sandbox: Literal["host", "docker"] = "host"
    require_approval: bool = False
```

**Step 2: Run test to verify it passes**

Run: `uv run ruff check src/superseded/models.py src/superseded/config.py`
Expected: PASS

**Step 3: Commit**

```bash
git add src/superseded/models.py src/superseded/config.py
git commit -m "feat(models): add approval pause reason and config options for sandbox/hitl"
```

### Task 2: Implement DockerAgentAdapter

**Files:**
- Create: `/home/debian/workspace/superseded/src/superseded/agents/docker.py`
- Modify: `/home/debian/workspace/superseded/src/superseded/agents/factory.py`

**Step 1: Write minimal implementation for DockerAgentAdapter**

In `agents/docker.py`, create a class that uses `docker run`:

```python
from __future__ import annotations

import os
from superseded.agents import register_agent
from superseded.agents.base import SubprocessAgentAdapter
from superseded.models import AgentContext

@register_agent("docker")
class DockerAgentAdapter(SubprocessAgentAdapter):
    def __init__(self, cli: str = "opencode", model: str = "", timeout: int = 600, github_token: str = "", api_key: str = "") -> None:
        super().__init__(timeout=timeout, github_token=github_token)
        self.cli = cli
        self.model = model
        self._api_key = api_key

    def _build_env(self) -> dict[str, str]:
        return super()._build_env()

    def _build_command(self, prompt: str) -> list[str]:
        # Implementation will wrap the inner CLI (e.g. opencode or claude) with docker run
        pass
        
    def _get_cwd(self, context: AgentContext) -> str:
        # Host directory to mount
        return context.worktree_path or context.repo_path
```

Implement the `_build_command` properly by mounting `cwd` to `/workspace` and running `uvx opencode` or `npx @anthropic-ai/claude-code`.

Update `agents/factory.py` to optionally wrap the returned adapter or return a `DockerAgentAdapter` if `sandbox == "docker"` in the configuration.

**Step 2: Commit**

```bash
git add src/superseded/agents/docker.py src/superseded/agents/factory.py
git commit -m "feat(agents): implement DockerAgentAdapter for sandboxed execution"
```

### Task 3: Handle Approvals in Executor

**Files:**
- Modify: `/home/debian/workspace/superseded/src/superseded/pipeline/executor.py`

**Step 1: Update executor logic**

In `StageExecutor._run_single_repo`, after the agent runs, check if `approval.md` exists in `repo_artifacts`.
If so, set pause reason to `"approval-required"`.
Additionally, if `stage_configs.get(stage.value).require_approval` is True, pause the pipeline *before* executing the agent or right after generating the plan, and create `approval.md`.

**Step 2: Commit**

```bash
git add src/superseded/pipeline/executor.py
git commit -m "feat(pipeline): handle approval.md and require_approval config in executor"
```

### Task 4: Add Web UI Endpoints for HITL

**Files:**
- Modify: `/home/debian/workspace/superseded/src/superseded/routes/web/issues.py`

**Step 1: Add endpoints**

Add POST `/issues/{issue_id}/approve` and POST `/issues/{issue_id}/reject`.
- `approve` should delete `approval.md`, clear the pause reason, and call `_run_and_advance`.
- `reject` should update the `approval.md` or add to `previous_errors` with the user's rejection reason, change pause reason to empty (or keep it failed), and restart the stage or send it back.

**Step 2: Commit**

```bash
git add src/superseded/routes/web/issues.py
git commit -m "feat(web): add approve and reject endpoints for HITL"
```

### Task 5: Add HTMX UI for Approvals

**Files:**
- Modify: `/home/debian/workspace/superseded/templates/issue_detail.html`

**Step 1: Add UI for Approvals**

In `issue_detail.html`, check if `issue.pause_reason == 'approval-required'`.
Display the contents of `approval.md` and render two forms (or buttons) for Approve and Reject that POST to the new endpoints.

**Step 2: Commit**

```bash
git add templates/issue_detail.html
git commit -m "feat(ui): add approval UI to issue detail page"
```
