# HITL and Docker Sandboxes Design

## Overview
This document outlines the design for integrating two major features into the Superseded pipeline:
1. Docker Sandbox Execution for isolating agents.
2. Human-In-The-Loop (HITL) Checkpoints for pausing the pipeline and requesting user approval before proceeding.

## 1. Docker Sandbox Execution
**Goal:** Run the agent CLI (Claude Code or OpenCode) in an isolated container instead of directly on the host machine.
**Approach:**
- **Standard Image:** We will use a standard Docker image (e.g. `python:3.12-slim` or `node:20`) and invoke the agent CLI using `uvx opencode` or `npx @anthropic-ai/claude-code`. This avoids maintaining a custom sandbox image.
- **Mounts:** The current repository worktree will be volume mounted to `/workspace` inside the container as read-write. Other sensitive paths (if any) could be read-only or not mounted.
- **Agent Adapter:** Introduce a `DockerAgentAdapter` that inherits from `SubprocessAgentAdapter`. This adapter modifies the command to `docker run ... uvx opencode ...` instead of directly calling `opencode`.
- **Configuration:** Add a setting `sandbox: "docker"` in the `.superseded/config.yaml` to trigger this mode instead of the default `host` execution.

## 2. Human-In-The-Loop (HITL) Checkpoints
**Goal:** Allow the pipeline to pause execution and ask the human user to approve or reject ambiguous or critical changes.
**Approach:**
- **Pause Reason:** Introduce a new `PauseReason` called `APPROVAL_REQUIRED` (or `awaiting-approval`).
- **Artifact:** Agents can generate an `approval.md` artifact (similar to `questions.md`). When the executor detects `approval.md`, it sets the pause reason to `awaiting-approval`.
- **Automatic Checkpoints:** Introduce a `require_approval: true` flag for pipeline stages (like `SHIP`) in `config.yaml`. If enabled, the executor automatically pauses the pipeline before running the stage and creates an `approval.md` containing a diff summary.
- **UI Integration (HTMX):** In `issue_detail.html`, if the pause reason is `awaiting-approval`, render "Approve" and "Reject" buttons.
- **Endpoints:** Add a new POST endpoint `/{issue_id}/approve` and `/{issue_id}/reject`.
    - **Approve:** Resumes the pipeline and moves to the next stage or completes the current stage.
    - **Reject:** Fails the current stage with a user-provided comment, looping it back for retry or previous stage.