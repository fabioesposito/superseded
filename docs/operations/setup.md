---
title: Development Setup
category: operations
summary: How to set up the development environment
tags: [setup, development, uv]
date: 2026-04-19
---

# Development Setup

## Prerequisites

- Python 3.14+
- Node.js (for Playwright tests)
- `uv` for dependency management

## Commands

```bash
uv sync                            # Install dependencies
uv run superseded                  # Start the server
uv run pytest tests/ -v           # Run all tests
uv run ruff check src/ tests/     # Lint
uv run ruff format src/ tests/    # Format
npx playwright test                # Run Playwright browser tests
```

See [Testing](testing.md) for a full overview of the test suite.

## Getting Started

### Quick Start

```bash
# Initialize .superseded/ in your project
uv run superseded init

# Start the server
uv run superseded
```

The `init` command scaffolds:
- `.superseded/config.yaml` with sensible defaults
- `.superseded/rules.md` template for agent instructions
- `.superseded/issues/` directory with an example ticket

### Environment Variables

API keys can be set via environment variables instead of config:
- `SUPERSEDED_API_KEY` — API key for Superseded itself
- `GITHUB_TOKEN` — GitHub authentication for PR creation
- `ANTHROPIC_API_KEY` — For Claude Code agent
- `OPENAI_API_KEY` — For Codex agent
- `OPENCODE_API_KEY` — For OpenCode agent

### Docker Prerequisites

If using `sandbox: docker` for any stage, Docker must be installed and the current user must have Docker permissions.
