---
title: Per-Stage CLI + Model Selection
category: adrs
summary: Per-Stage CLI + Model Selection
tags: []
date: 2026-04-12
---

# Per-Stage CLI + Model Selection

## Overview

Allow users to pick which CLI (claude-code, opencode, codex) and which model to use for each pipeline stage. Config lives in `.superseded/config.yaml`, editable from `/settings` UI.

## Config Schema

```yaml
# .superseded/config.yaml
default_agent: claude-code
default_model: ""

stages:
  spec:
    cli: claude-code
    model: claude-sonnet-4-20250514
  plan:
    cli: claude-code
    model: claude-sonnet-4-20250514
  build:
    cli: opencode
    model: gpt-4o
  verify:
    cli: opencode
    model: gpt-4o
  review:
    cli: claude-code
    model: claude-sonnet-4-20250514
  ship:
    cli: claude-code
    model: ""
```

Stages not listed fall back to `default_agent` + `default_model`.

New Pydantic model `StageAgentConfig(cli: str, model: str)` added to `config.py`. `SupersededConfig` gains `stages: dict[str, StageAgentConfig]` and `default_model: str`.

## Agent Adapters

All adapters gain a `model: str` parameter. If non-empty, appended as `--model <model>` to the CLI command.

### CodexAdapter (new)

- File: `src/superseded/agents/codex.py`
- Command: `codex --quiet` (+ `--model` if set)
- Prompt via stdin

### ClaudeCodeAdapter (modify)

- Add `model` param to `__init__`
- Append `--model <model>` to command if set

### OpenCodeAdapter (modify)

- Add `model` param to `__init__`
- Append `--model <model>` to command if set

## AgentFactory

New file: `src/superseded/agents/factory.py`

```python
class AgentFactory:
    def __init__(self, default_agent, default_model, timeout)
    def create(self, cli=None, model=None) -> AgentAdapter
```

Maps CLI name to adapter class. Returns configured instance.

## HarnessRunner Changes

- Replace `agent: AgentAdapter` with `agent_factory: AgentFactory` + `stage_configs: dict[str, StageAgentConfig]`
- Add `resolve_agent(stage: Stage) -> AgentAdapter`
- Call `resolve_agent` at start of `run_stage_with_retries` and `run_stage_streaming`

## Wiring (main.py)

- `_build_pipeline_state` creates `AgentFactory` from config defaults
- `HarnessRunner` receives factory + `config.stages`
- Remove hardcoded `ClaudeCodeAdapter`

## Settings UI

- New "Pipeline Agents" section on `/settings` page
- Table: one row per stage, dropdown for CLI, text input for model
- POST to `/settings/agents` → saves config, reloads pipeline
- Template: `templates/_agents_table.html`

## Files to Change

1. `src/superseded/config.py` — add `StageAgentConfig`, `stages` + `default_model` fields
2. `src/superseded/agents/claude_code.py` — add `model` param
3. `src/superseded/agents/opencode.py` — add `model` param
4. `src/superseded/agents/codex.py` — new file
5. `src/superseded/agents/factory.py` — new file
6. `src/superseded/pipeline/harness.py` — factory + resolve_agent
7. `src/superseded/main.py` — wire factory
8. `src/superseded/routes/settings.py` — agents endpoint
9. `templates/settings.html` — agents section
10. `templates/_agents_table.html` — new partial

## Testing

- Unit tests for `AgentFactory.create` with each CLI + model combo
- Unit tests for adapter `_build_command` with model param
- Integration test for `HarnessRunner.resolve_agent` with stage configs
- Playwright test for settings UI agents table
