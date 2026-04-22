---
title: Development Setup
category: operations
summary: How to set up the development environment
tags: [setup, development, uv]
date: 2026-04-19
---

# Development Setup

## Prerequisites

- Python 3.12+
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
