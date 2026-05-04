---
title: Troubleshooting
category: operations
summary: Common issues and fixes
tags: [troubleshooting, debugging]
date: 2026-04-19
---

# Troubleshooting

## Agent Issues

### Agent silent or unresponsive
The health monitor detects agents that produce no output for > 5 minutes. Check:
- Is the agent subprocess still alive? (`/health` endpoint shows `active_stages`)
- Is the API key valid? Check Settings → Setup wizard
- Is the model available? Some models have rate limits

### Agent produced insufficient output
The harness rejects runs with < 50 characters of output. This usually means the agent didn't perform the work. Retry the stage.

## Verification Issues

### Missing required sections
The verification engine checks that artifact markdown contains required sections (e.g., `## Problem`, `## Solution`). Add the missing sections to your spec/plan template.

### Critical findings block merge
The review stage found Critical-severity issues. Fix the issues or adjust `max_critical_findings` in config.

### Tests failed
The verify stage parsed test output and found failures. Fix the failing tests.

## Approval Issues

### Stage paused with "approval-required"
The stage requires manual approval. Click Approve or Reject in the UI. To auto-skip approval, set `require_approval: false` in config.

### File-level review approval
During the Review stage, individual files can be approved/rejected. All files must be approved before the stage advances.

## Recovery Issues

### Server restarted during execution
On startup, Superseded marks in-progress issues as paused. If a checkpoint exists, the stage can be resumed. Otherwise, retry from scratch.

### Checkpoint preconditions failed
External changes (file modifications, git operations) invalidated the checkpoint. The checkpoint is discarded and the stage restarts.

## Docker Issues

### Docker not installed
Docker sandboxing requires Docker. Install it or switch to `sandbox: host` in config.

### Container resource limits
Docker containers get 2GB memory, 2 CPUs, 256 PIDs by default. Adjust in config if needed.

## Notification Issues

### ntfy.sh notifications not arriving
Check that `notifications.enabled: true` and `notifications.ntfy_topic` is set in config.

### Slack webhook failing
Verify the webhook URL is correct. Check Slack app permissions.

## Database Issues

### Migration errors
Superseded uses Alembic for schema migrations. If migrations fail, check that the database file is writable.
