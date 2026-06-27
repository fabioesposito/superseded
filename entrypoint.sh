#!/bin/bash
set -euo pipefail

# SUPERSEDED_AGENT / SUPERSEDED_MODEL are forwarded from action.yml inputs and
# override the CLI flags (env precedence > flags, see cli.resolve_agent). Fall
# back to INPUT_* then empty so the tool auto-detects when nothing is supplied.
AGENT="${SUPERSEDED_AGENT:-${INPUT_AGENT:-}}"
MODEL="${SUPERSEDED_MODEL:-${INPUT_MODEL:-}}"
PASSES="${INPUT_PASSES:-security,correctness,performance,style,architecture}"
POST="${INPUT_POST:-true}"

PR_NUMBER="${GITHUB_EVENT_PULL_REQUEST_NUMBER:-}"

if [ -z "$PR_NUMBER" ]; then
    echo "Error: GITHUB_EVENT_PULL_REQUEST_NUMBER is not set; this action must run on a pull_request event." >&2
    exit 1
fi

if [ -z "$AGENT" ]; then
    echo "No agent specified; superseded will auto-detect the highest-preference AI CLI installed." >&2
fi

# Validate the chosen AI CLI is on PATH before invoking superseded, so failures
# surface as a clear message instead of a per-pass RuntimeError stack trace.
BINARY="${AGENT}"
case "$AGENT" in
    claude-code) BINARY="claude" ;;
    opencode)   BINARY="opencode" ;;
    codex)      BINARY="codex" ;;
    "")         BINARY="" ;;
    *)          BINARY="$AGENT" ;;
esac

if [ -n "$BINARY" ] && ! command -v "$BINARY" >/dev/null 2>&1; then
    echo "Error: agent CLI '$BINARY' (for agent '$AGENT') was not found on PATH." >&2
    echo "Install it in the Docker image or set 'agent:' to a CLI that is installed." >&2
    exit 1
fi

CMD=(superseded review --pr "$PR_NUMBER" --passes "$PASSES")

# Only pass --agent / --model when explicitly set; otherwise let superseded use
# its config / auto-detection defaults. Env vars (SUPERSEDED_AGENT/MODEL) still
# take precedence over these flags per cli.resolve_*.
if [ -n "$AGENT" ]; then
    CMD+=(--agent "$AGENT")
fi
if [ -n "$MODEL" ]; then
    CMD+=(--model "$MODEL")
fi

if [ "$POST" = "true" ]; then
    CMD+=(--post)
fi

echo "Running: ${CMD[*]}"
exec "${CMD[@]}"
