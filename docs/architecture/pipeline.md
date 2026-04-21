---
title: Pipeline Engine
category: architecture
summary: Pipeline stage flow, retry logic, and worktree isolation
tags: [pipeline, stages, worktree]
date: 2026-04-19
---

# Pipeline Engine

The pipeline processes tickets through six stages: Spec → Plan → Build → Verify → Review → Ship.

## Stage Flow

Each stage runs as an isolated agent subprocess. The agent receives progressive context
(layers of increasing specificity) and writes artifacts to `.superseded/artifacts/{id}/`.

## Retry Logic

Failed stages pause the pipeline. You can retry manually from the web UI.
Error context from previous attempts is injected into the re-prompt.

## Worktree Isolation

BUILD, VERIFY, and REVIEW stages run in isolated git worktrees. Changes merge on
success, discard on failure.
