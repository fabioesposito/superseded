---
title: Agent Harness
category: architecture
summary: How the harness orchestrates multi-agent pipelines with feedback loops
tags: [harness, agents, orchestration]
date: 2026-04-19
---

# Agent Harness

Superseded is an agent harness that orchestrates AI agents through a structured pipeline.

## Features

- **Feedback loops**: Stages retry on failure with error context
- **Execution plans**: Plan stage writes structured plan.md consumed by downstream stages
- **Progressive context**: Agents receive context in layers (AGENTS.md → docs → ticket → artifacts → rules → skill prompt)
- **Worktree isolation**: Changes are sandboxed until success
- **Quality enforcement**: Review findings loop back to BUILD
- **Iteration history**: Every attempt tracked in database and UI
