#!/bin/bash
set -e

AGENT="${INPUT_AGENT:-claude-code}"
MODEL="${INPUT_MODEL:-}"
PASSES="${INPUT_PASSES:-security,correctness,performance,style,architecture}"
POST="${INPUT_POST:-true}"

PR_NUMBER="${GITHUB_EVENT_PULL_REQUEST_NUMBER}"

if [ -z "$PR_NUMBER" ]; then
    echo "Error: Not a pull request event."
    exit 1
fi

CMD="superseded review --pr $PR_NUMBER --agent $AGENT --passes $PASSES"

if [ -n "$MODEL" ]; then
    CMD="$CMD --model $MODEL"
fi

if [ "$POST" = "true" ]; then
    CMD="$CMD --post"
fi

echo "Running: $CMD"
eval "$CMD"
