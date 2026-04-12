# Per-Stage Agent + Model Selection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow per-stage CLI (claude-code/opencode/codex) and model selection via config.yaml and settings UI.

**Architecture:** AgentFactory builds adapters from CLI name + model. HarnessRunner resolves the correct agent per stage via stage_configs dict. Config lives in `.superseded/config.yaml` with fallback to defaults.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, HTMX, Pydantic, yaml

---

### Task 1: Add StageAgentConfig to config model

**Files:**
- Modify: `src/superseded/config.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

```python
# Add to tests/test_config.py
from superseded.config import StageAgentConfig, SupersededConfig

def test_stage_agent_config_defaults():
    cfg = StageAgentConfig()
    assert cfg.cli == "claude-code"
    assert cfg.model == ""

def test_stage_agent_config_custom():
    cfg = StageAgentConfig(cli="opencode", model="gpt-4o")
    assert cfg.cli == "opencode"
    assert cfg.model == "gpt-4o"

def test_superseded_config_stages_default():
    cfg = SupersededConfig()
    assert cfg.stages == {}
    assert cfg.default_model == ""

def test_superseded_config_stages_populated():
    cfg = SupersededConfig(stages={
        "build": StageAgentConfig(cli="opencode", model="gpt-4o"),
    })
    assert cfg.stages["build"].cli == "opencode"
    assert cfg.stages["build"].model == "gpt-4o"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v -k "stage_agent"`
Expected: FAIL with `ImportError` or `AttributeError`

**Step 3: Write implementation**

In `src/superseded/config.py`, add:

```python
class StageAgentConfig(BaseModel):
    cli: str = "claude-code"
    model: str = ""
```

Add to `SupersededConfig`:
```python
default_model: str = ""
stages: dict[str, StageAgentConfig] = Field(default_factory=dict)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v -k "stage_agent"`
Expected: PASS

**Step 5: Run linter**

Run: `uv run ruff check src/superseded/config.py`
Expected: no errors

**Step 6: Commit**

```bash
git add src/superseded/config.py tests/test_config.py
git commit -m "feat(config): add StageAgentConfig and stages field"
```

---

### Task 2: Add model support to ClaudeCodeAdapter

**Files:**
- Modify: `src/superseded/agents/claude_code.py`
- Test: `tests/test_agents.py`

**Step 1: Write the failing test**

```python
# Create tests/test_agents.py
from __future__ import annotations

from superseded.agents.claude_code import ClaudeCodeAdapter

def test_claude_code_no_model():
    adapter = ClaudeCodeAdapter()
    cmd = adapter._build_command("test prompt")
    assert cmd == ["claude", "--print", "--output-format", "text"]

def test_claude_code_with_model():
    adapter = ClaudeCodeAdapter(model="claude-sonnet-4-20250514")
    cmd = adapter._build_command("test prompt")
    assert cmd == ["claude", "--print", "--output-format", "text", "--model", "claude-sonnet-4-20250514"]

def test_claude_code_with_timeout():
    adapter = ClaudeCodeAdapter(timeout=300)
    assert adapter.timeout == 300

def test_claude_code_stdin():
    adapter = ClaudeCodeAdapter()
    data = adapter._get_stdin_data("hello")
    assert data == b"hello"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agents.py -v -k "claude_code"`
Expected: FAIL — `ClaudeCodeAdapter.__init__` doesn't accept `model`

**Step 3: Write implementation**

In `src/superseded/agents/claude_code.py`:

```python
from __future__ import annotations

from superseded.agents.base import SubprocessAgentAdapter


class ClaudeCodeAdapter(SubprocessAgentAdapter):
    def __init__(self, model: str = "", timeout: int = 600) -> None:
        super().__init__(timeout=timeout)
        self.model = model

    def _build_command(self, prompt: str) -> list[str]:
        cmd = ["claude", "--print", "--output-format", "text"]
        if self.model:
            cmd.extend(["--model", self.model])
        return cmd

    def _get_stdin_data(self, prompt: str) -> bytes | None:
        return prompt.encode("utf-8")
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_agents.py -v -k "claude_code"`
Expected: PASS

**Step 5: Commit**

```bash
git add src/superseded/agents/claude_code.py tests/test_agents.py
git commit -m "feat(agents): add model param to ClaudeCodeAdapter"
```

---

### Task 3: Add model support to OpenCodeAdapter

**Files:**
- Modify: `src/superseded/agents/opencode.py`
- Test: `tests/test_agents.py`

**Step 1: Write the failing test**

Add to `tests/test_agents.py`:

```python
from superseded.agents.opencode import OpenCodeAdapter

def test_opencode_no_model():
    adapter = OpenCodeAdapter()
    cmd = adapter._build_command("test prompt")
    assert cmd == ["opencode", "--non-interactive"]

def test_opencode_with_model():
    adapter = OpenCodeAdapter(model="gpt-4o")
    cmd = adapter._build_command("test prompt")
    assert cmd == ["opencode", "--non-interactive", "--model", "gpt-4o"]

def test_opencode_stdin():
    adapter = OpenCodeAdapter()
    data = adapter._get_stdin_data("hello")
    assert data == b"hello"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agents.py -v -k "opencode"`
Expected: FAIL — `OpenCodeAdapter.__init__` doesn't accept `model`

**Step 3: Write implementation**

In `src/superseded/agents/opencode.py`:

```python
from __future__ import annotations

from superseded.agents.base import SubprocessAgentAdapter


class OpenCodeAdapter(SubprocessAgentAdapter):
    def __init__(self, model: str = "", timeout: int = 600) -> None:
        super().__init__(timeout=timeout)
        self.model = model

    def _build_command(self, prompt: str) -> list[str]:
        cmd = ["opencode", "--non-interactive"]
        if self.model:
            cmd.extend(["--model", self.model])
        return cmd

    def _get_stdin_data(self, prompt: str) -> bytes | None:
        return prompt.encode("utf-8")
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_agents.py -v -k "opencode"`
Expected: PASS

**Step 5: Commit**

```bash
git add src/superseded/agents/opencode.py tests/test_agents.py
git commit -m "feat(agents): add model param to OpenCodeAdapter"
```

---

### Task 4: Create CodexAdapter

**Files:**
- Create: `src/superseded/agents/codex.py`
- Test: `tests/test_agents.py`

**Step 1: Write the failing test**

Add to `tests/test_agents.py`:

```python
from superseded.agents.codex import CodexAdapter

def test_codex_no_model():
    adapter = CodexAdapter()
    cmd = adapter._build_command("test prompt")
    assert cmd == ["codex", "--quiet"]

def test_codex_with_model():
    adapter = CodexAdapter(model="o4-mini")
    cmd = adapter._build_command("test prompt")
    assert cmd == ["codex", "--quiet", "--model", "o4-mini"]

def test_codex_with_timeout():
    adapter = CodexAdapter(timeout=300)
    assert adapter.timeout == 300

def test_codex_stdin():
    adapter = CodexAdapter()
    data = adapter._get_stdin_data("hello")
    assert data == b"hello"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agents.py -v -k "codex"`
Expected: FAIL — `CodexAdapter` not found

**Step 3: Write implementation**

Create `src/superseded/agents/codex.py`:

```python
from __future__ import annotations

from superseded.agents.base import SubprocessAgentAdapter


class CodexAdapter(SubprocessAgentAdapter):
    def __init__(self, model: str = "", timeout: int = 600) -> None:
        super().__init__(timeout=timeout)
        self.model = model

    def _build_command(self, prompt: str) -> list[str]:
        cmd = ["codex", "--quiet"]
        if self.model:
            cmd.extend(["--model", self.model])
        return cmd

    def _get_stdin_data(self, prompt: str) -> bytes | None:
        return prompt.encode("utf-8")
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_agents.py -v -k "codex"`
Expected: PASS

**Step 5: Run full agent tests**

Run: `uv run pytest tests/test_agents.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/superseded/agents/codex.py tests/test_agents.py
git commit -m "feat(agents): add CodexAdapter"
```

---

### Task 5: Create AgentFactory

**Files:**
- Create: `src/superseded/agents/factory.py`
- Test: `tests/test_agents.py`

**Step 1: Write the failing test**

Add to `tests/test_agents.py`:

```python
from superseded.agents.factory import AgentFactory
from superseded.agents.claude_code import ClaudeCodeAdapter
from superseded.agents.opencode import OpenCodeAdapter
from superseded.agents.codex import CodexAdapter

def test_factory_default():
    factory = AgentFactory()
    agent = factory.create()
    assert isinstance(agent, ClaudeCodeAdapter)
    assert agent.model == ""

def test_factory_claude_with_model():
    factory = AgentFactory()
    agent = factory.create(cli="claude-code", model="claude-sonnet-4-20250514")
    assert isinstance(agent, ClaudeCodeAdapter)
    assert agent.model == "claude-sonnet-4-20250514"

def test_factory_opencode():
    factory = AgentFactory()
    agent = factory.create(cli="opencode", model="gpt-4o")
    assert isinstance(agent, OpenCodeAdapter)
    assert agent.model == "gpt-4o"

def test_factory_codex():
    factory = AgentFactory()
    agent = factory.create(cli="codex", model="o4-mini")
    assert isinstance(agent, CodexAdapter)
    assert agent.model == "o4-mini"

def test_factory_custom_defaults():
    factory = AgentFactory(default_agent="opencode", default_model="gpt-4o", timeout=300)
    agent = factory.create()
    assert isinstance(agent, OpenCodeAdapter)
    assert agent.model == "gpt-4o"
    assert agent.timeout == 300

def test_factory_unknown_cli():
    factory = AgentFactory()
    import pytest
    with pytest.raises(ValueError, match="Unknown agent CLI: bad"):
        factory.create(cli="bad")
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agents.py -v -k "factory"`
Expected: FAIL — `AgentFactory` not found

**Step 3: Write implementation**

Create `src/superseded/agents/factory.py`:

```python
from __future__ import annotations

from superseded.agents.base import AgentAdapter
from superseded.agents.claude_code import ClaudeCodeAdapter
from superseded.agents.codex import CodexAdapter
from superseded.agents.opencode import OpenCodeAdapter


class AgentFactory:
    def __init__(
        self,
        default_agent: str = "claude-code",
        default_model: str = "",
        timeout: int = 600,
    ) -> None:
        self.default_agent = default_agent
        self.default_model = default_model
        self.timeout = timeout

    def create(self, cli: str | None = None, model: str | None = None) -> AgentAdapter:
        cli = cli or self.default_agent
        model = model or self.default_model
        if cli == "claude-code":
            return ClaudeCodeAdapter(model=model, timeout=self.timeout)
        elif cli == "opencode":
            return OpenCodeAdapter(model=model, timeout=self.timeout)
        elif cli == "codex":
            return CodexAdapter(model=model, timeout=self.timeout)
        raise ValueError(f"Unknown agent CLI: {cli}")
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_agents.py -v -k "factory"`
Expected: PASS

**Step 5: Run linter**

Run: `uv run ruff check src/superseded/agents/factory.py`
Expected: no errors

**Step 6: Commit**

```bash
git add src/superseded/agents/factory.py tests/test_agents.py
git commit -m "feat(agents): add AgentFactory"
```

---

### Task 6: Update HarnessRunner to use AgentFactory

**Files:**
- Modify: `src/superseded/pipeline/harness.py`
- Test: `tests/test_harness.py`

**Step 1: Write the failing test**

Create/update `tests/test_harness.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from superseded.agents.factory import AgentFactory
from superseded.agents.claude_code import ClaudeCodeAdapter
from superseded.agents.opencode import OpenCodeAdapter
from superseded.config import StageAgentConfig
from superseded.models import Stage
from superseded.pipeline.harness import HarnessRunner


def test_resolve_agent_default():
    factory = AgentFactory(default_agent="claude-code", default_model="")
    runner = HarnessRunner(
        agent_factory=factory,
        repo_path="/tmp/test",
    )
    agent = runner.resolve_agent(Stage.SPEC)
    assert isinstance(agent, ClaudeCodeAdapter)


def test_resolve_agent_stage_override():
    factory = AgentFactory(default_agent="claude-code", default_model="")
    runner = HarnessRunner(
        agent_factory=factory,
        repo_path="/tmp/test",
        stage_configs={
            "build": StageAgentConfig(cli="opencode", model="gpt-4o"),
        },
    )
    agent = runner.resolve_agent(Stage.BUILD)
    assert isinstance(agent, OpenCodeAdapter)
    assert agent.model == "gpt-4o"


def test_resolve_agent_falls_back_to_default():
    factory = AgentFactory(default_agent="claude-code", default_model="sonnet")
    runner = HarnessRunner(
        agent_factory=factory,
        repo_path="/tmp/test",
        stage_configs={
            "build": StageAgentConfig(cli="opencode", model="gpt-4o"),
        },
    )
    agent = runner.resolve_agent(Stage.SPEC)
    assert isinstance(agent, ClaudeCodeAdapter)
    assert agent.model == "sonnet"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_harness.py -v -k "resolve_agent"`
Expected: FAIL — HarnessRunner doesn't accept `agent_factory`

**Step 3: Write implementation**

Refactor `src/superseded/pipeline/harness.py`:

- Change `__init__` to accept `agent_factory: AgentFactory` and `stage_configs: dict[str, StageAgentConfig]` instead of `agent: AgentAdapter`
- Keep backward compat by accepting `agent` as optional (for existing tests)
- Add `resolve_agent(self, stage: Stage) -> AgentAdapter`
- Update `run_stage_with_retries` and `run_stage_streaming` to call `resolve_agent`

```python
def __init__(
    self,
    agent_factory: AgentFactory | None = None,
    repo_path: str = "",
    max_retries: int = 3,
    retryable_stages: list[str] | None = None,
    event_manager: PipelineEventManager | None = None,
    stage_configs: dict[str, StageAgentConfig] | None = None,
    # Backward compat
    agent: AgentAdapter | None = None,
) -> None:
    if agent_factory is None and agent is not None:
        from superseded.agents.factory import AgentFactory
        agent_factory = AgentFactory()
        # Wrap single agent in a factory that always returns it
        agent_factory.create = lambda **_: agent  # type: ignore
    self.agent_factory = agent_factory
    self.stage_configs = stage_configs or {}
    # ... rest of init

def resolve_agent(self, stage: Stage) -> AgentAdapter:
    config = self.stage_configs.get(stage.value)
    if config:
        return self.agent_factory.create(cli=config.cli, model=config.model)
    return self.agent_factory.create()
```

Update `run_stage_with_retries` to call `self.resolve_agent(stage)` instead of using `self.agent` directly. Same for `run_stage_streaming`.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_harness.py -v -k "resolve_agent"`
Expected: PASS

**Step 5: Run existing tests to check no regressions**

Run: `uv run pytest tests/ -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/superseded/pipeline/harness.py tests/test_harness.py
git commit -m "feat(harness): per-stage agent resolution via AgentFactory"
```

---

### Task 7: Wire AgentFactory in main.py

**Files:**
- Modify: `src/superseded/main.py`

**Step 1: Update `_build_pipeline_state`**

Replace the hardcoded `ClaudeCodeAdapter` with `AgentFactory`:

```python
from superseded.agents.factory import AgentFactory

def _build_pipeline_state(config: SupersededConfig) -> PipelineState:
    event_manager = PipelineEventManager()
    factory = AgentFactory(
        default_agent=config.default_agent,
        default_model=config.default_model,
        timeout=config.stage_timeout_seconds,
    )
    runner = HarnessRunner(
        agent_factory=factory,
        repo_path=config.repo_path,
        max_retries=config.max_retries,
        retryable_stages=config.retryable_stages,
        event_manager=event_manager,
        stage_configs=config.stages,
    )
    # ... rest unchanged
```

Remove the `ClaudeCodeAdapter` import from main.py.

**Step 2: Run tests**

Run: `uv run pytest tests/ -v`
Expected: All PASS

**Step 3: Run linter**

Run: `uv run ruff check src/superseded/main.py`
Expected: no errors

**Step 4: Commit**

```bash
git add src/superseded/main.py
git commit -m "feat: wire AgentFactory in main.py"
```

---

### Task 8: Add Settings UI — agents table template

**Files:**
- Create: `templates/_agents_table.html`
- Modify: `templates/settings.html`

**Step 1: Create the agents table partial**

Create `templates/_agents_table.html`:

```html
<div id="agents-config">
  <h3>Pipeline Agents</h3>
  {% if error %}
  <div class="error">{{ error }}</div>
  {% endif %}
  <form hx-post="/settings/agents" hx-target="#agents-config" hx-swap="outerHTML">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}" />
    <table>
      <thead>
        <tr>
          <th>Stage</th>
          <th>CLI</th>
          <th>Model</th>
        </tr>
      </thead>
      <tbody>
        {% set stages = ['spec', 'plan', 'build', 'verify', 'review', 'ship'] %}
        {% for stage in stages %}
        <tr>
          <td>{{ stage | capitalize }}</td>
          <td>
            <select name="{{ stage }}_cli">
              <option value="claude-code" {% if stage_agents[stage].cli == 'claude-code' %}selected{% endif %}>Claude Code</option>
              <option value="opencode" {% if stage_agents[stage].cli == 'opencode' %}selected{% endif %}>OpenCode</option>
              <option value="codex" {% if stage_agents[stage].cli == 'codex' %}selected{% endif %}>Codex</option>
            </select>
          </td>
          <td>
            <input type="text" name="{{ stage }}_model" value="{{ stage_agents[stage].model | default('') }}" placeholder="e.g. claude-sonnet-4-20250514" />
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    <button type="submit">Save Agents</button>
  </form>
</div>
```

**Step 2: Add agents section to settings.html**

In `templates/settings.html`, add after the repos section:

```html
{% include "_agents_table.html" %}
```

**Step 3: Commit**

```bash
git add templates/_agents_table.html templates/settings.html
git commit -m "feat(ui): add pipeline agents settings table"
```

---

### Task 9: Add Settings route for agents

**Files:**
- Modify: `src/superseded/routes/settings.py`
- Modify: `templates/settings.html`

**Step 1: Update settings_page to pass stage_agents**

```python
@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, deps: Deps = Depends(get_deps)):
    repos = deps.config.repos
    stages = ["spec", "plan", "build", "verify", "review", "ship"]
    stage_agents = {}
    for stage in stages:
        stage_agents[stage] = deps.config.stages.get(stage, StageAgentConfig())
    return get_templates().TemplateResponse(
        request,
        "settings.html",
        {
            "repos": repos,
            "stage_agents": stage_agents,
        },
    )
```

**Step 2: Add POST endpoint**

```python
@router.post("/settings/agents", response_class=HTMLResponse)
async def update_agents(
    request: Request,
    deps: Deps = Depends(get_deps),
    spec_cli: str = Form("claude-code"),
    spec_model: str = Form(""),
    plan_cli: str = Form("claude-code"),
    plan_model: str = Form(""),
    build_cli: str = Form("claude-code"),
    build_model: str = Form(""),
    verify_cli: str = Form("claude-code"),
    verify_model: str = Form(""),
    review_cli: str = Form("claude-code"),
    review_model: str = Form(""),
    ship_cli: str = Form("claude-code"),
    ship_model: str = Form(""),
):
    from superseded.config import StageAgentConfig, save_config

    config = deps.config
    stages_data = {
        "spec": StageAgentConfig(cli=spec_cli, model=spec_model),
        "plan": StageAgentConfig(cli=plan_cli, model=plan_model),
        "build": StageAgentConfig(cli=build_cli, model=build_model),
        "verify": StageAgentConfig(cli=verify_cli, model=verify_model),
        "review": StageAgentConfig(cli=review_cli, model=review_model),
        "ship": StageAgentConfig(cli=ship_cli, model=ship_model),
    }
    config.stages = stages_data
    save_config(config, Path(config.repo_path))
    _reload_pipeline(request.app, config)

    stage_agents = {k: v for k, v in stages_data.items()}
    return get_templates().TemplateResponse(
        request,
        "_agents_table.html",
        {"stage_agents": stage_agents},
    )
```

**Step 3: Run tests**

Run: `uv run pytest tests/ -v`
Expected: All PASS

**Step 4: Run linter**

Run: `uv run ruff check src/superseded/routes/settings.py`
Expected: no errors

**Step 5: Commit**

```bash
git add src/superseded/routes/settings.py
git commit -m "feat(settings): add agents config endpoint"
```

---

### Task 10: Run full test suite and lint

**Step 1: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All PASS

**Step 2: Run linter**

Run: `uv run ruff check src/ tests/`
Expected: no errors

**Step 3: Run formatter check**

Run: `uv run ruff format --check src/ tests/`
Expected: no changes needed

**Step 4: Final commit if needed**

If any fixes required from lint/format:
```bash
git add -A
git commit -m "chore: lint and format fixes"
```
