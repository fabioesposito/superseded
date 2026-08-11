# Direct-API Provider (DeepSeek) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the subprocess-based AI CLI agent layer (claude-code/codex/opencode) with a direct DeepSeek API call via the `openai` SDK, behind a new `Provider` abstraction, and rip out the now-dead executor/sandbox/skill/detection machinery.

**Architecture:** A new `providers/` package owns the `Provider` protocol, `DeepSeekProvider` (the first implementation), and JSON parsing. `ReviewEngine` takes a `Provider` instead of an `Agent`; each of the 5 concurrent passes becomes one HTTP call. The `Session`/`AgentExecutor`/`SubprocessExecutor`/`SandboxExecutor`/`SmolvmExecutor` machinery in `review/executor.py` (966 lines) is deleted along with `agents/`, `skill.py`, and `detection.py`. Future OpenAI/Anthropic/local providers are one class each.

**Tech Stack:** Python 3.14+, `openai` SDK (new dep), `httpx` (existing), click, pydantic v2, pytest. Run everything via `uv run`. Async tests run without `@pytest.mark.asyncio` (config: `asyncio_mode = "auto"`).

**Spec:** `docs/superseded/specs/2026-08-11-direct-api-provider-design.md`

---

## File Structure

**Create:**
- `src/superseded/providers/__init__.py` — exports `Provider`, `DeepSeekProvider`, `PROVIDER_MAP`, `ProviderResponse`, `ProviderConfigError`.
- `src/superseded/providers/base.py` — `Provider` Protocol, `ProviderResponse` dataclass, `ProviderConfigError`.
- `src/superseded/providers/parsing.py` — `_parse_findings_json(text, pass_name)`.
- `src/superseded/providers/deepseek.py` — `DeepSeekProvider` + `DEEPSEEK_*` constants.
- `tests/test_providers.py` — unit tests for base/parsing/deepseek (single file matching the existing flat `tests/` layout).
- `tests/test_live_deepseek.py` — `@pytest.mark.live` round-trip tests, gated on `SUPERSEDED_DEEPSEEK_API_KEY`.
- `MIGRATION.md` — repo-root migration guide for the v0.5 → v0.6 breaking change.

**Modify:**
- `pyproject.toml` — add `openai>=1.50.0` runtime dep; remove `optional-dependencies.sandbox`; bump version `0.5.0` → `0.6.0`; update `addopts` to also skip `live` marker.
- `src/superseded/models.py` — add `ReviewUsage` model and `ReviewResult.usage` field.
- `src/superseded/review/engine.py` — replace `Agent`/`AGENT_MAP`/`Session`/executor wiring with `Provider`/`PROVIDER_MAP`; `_run_and_validate` calls `provider.complete()` + `_parse_findings_json()`; drop `sess`/`cwd`/`env`/`executor` from `review()`; accumulate token usage into `ReviewResult.usage`.
- `src/superseded/config.py` — rename `Config.agent` → `Config.provider` (default `"deepseek"`); remove `Config.sandbox`; `load_config` hard-errors on legacy `agent: claude-code|opencode|codex` values.
- `src/superseded/cli.py` — rename `--agent` → `--provider` (and `SUPERSEDED_AGENT` → `SUPERSEDED_PROVIDER` with deprecation alias); remove `--sandbox`/`--no-sandbox`, `_select_executor`, `_resolve_smolvm_image`, `resolve_sandbox`, the serve-time sandbox wiring block, and the entire `skill` command group; rewrite `_run_init` to skip agent probing and check for `SUPERSEDED_DEEPSEEK_API_KEY`.
- `src/superseded/server/config.py` — drop `sandbox_*` and `smolvm_*` fields + their env loaders; add `deepseek_api_key` field loaded from `SUPERSEDED_DEEPSEEK_API_KEY`; refuse-to-start check replaces the old sandbox-presence check.
- `src/superseded/server/worker.py` — construct `DeepSeekProvider(api_key=...)` once; drop `SandboxSettings`, `_agent_smolvm_image`, `_sandbox_unavailable_msg`, the sandbox-branch at worker.py:498-523.
- `tests/test_engine.py` — port all engine tests from fake `Agent` to fake `Provider`; delete tests that exercised the executor path (`test_review_raises_when_agent_unavailable`, `test_review_defaults_to_subprocess_executor`, `test_review_fallback_executor_forwards_agent_name`, `test_review_uses_injected_executor_session`, `test_review_forwards_conventions_and_spec_signals` shape stays but uses Provider).
- `tests/test_cli.py`, `tests/test_server_worker.py`, `tests/test_server_config.py`, `tests/test_init.py`, `tests/test_integration.py` — update for new CLI surface and provider-based engine construction.
- `tests/test_config.py` — add coverage for legacy `agent:` YAML hard-error and `sandbox:` warn-and-ignore.
- `README.md` — swap setup instructions from "install claude-code/codex/opencode" to "set `SUPERSEDED_DEEPSEEK_API_KEY`".
- `AGENTS.md` — rewrite Architecture notes for Provider/PROVIDER_MAP; document deletions; preserve the `except A, B:` note.
- `action.yml` — document `SUPERSEDED_DEEPSEEK_API_KEY` as a required Action secret.

**Delete (entire files):**
- `src/superseded/agents/__init__.py`, `base.py`, `claude_code.py`, `codex.py`, `opencode.py`, `parsing.py`.
- `src/superseded/review/executor.py`.
- `src/superseded/skill.py`.
- `src/superseded/detection.py`.
- `tests/test_agents.py`, `tests/test_executor.py`, `tests/test_skill.py`, `tests/test_detection.py`.

**Net change:** roughly −1500 lines deleted, +200 added across the new `providers/` package and the engine/config edits.

---

### Task 1: Add `openai` SDK dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the dep**

Edit `pyproject.toml`'s `[project] dependencies = [...]` list to insert `"openai>=1.50.0",` (alphabetical order: insert between `"alembic>=1.13.0",` and `"click>=8.1.0",`).

- [ ] **Step 2: Sync the lockfile**

Run: `uv sync`
Expected: `uv.lock` updated to include `openai` and its transitive deps. `.venv/` now has the package.

- [ ] **Step 3: Verify import works**

Run: `uv run python -c "from openai import OpenAI; print(OpenAI)"`
Expected: `<class 'openai.OpenAI'>` (no `ImportError`).

- [ ] **Step 4: Verify baseline test suite still passes**

Run: `uv run pytest tests/ -q`
Expected: same pass count as before this task (the dep add is invisible to existing tests).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): add openai SDK for direct DeepSeek API access"
```

---

### Task 2: Create `providers/base.py` — Protocol, ProviderResponse, ProviderConfigError

**Files:**
- Create: `src/superseded/providers/__init__.py` (empty placeholder for now)
- Create: `src/superseded/providers/base.py`
- Create: `tests/test_providers.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_providers.py`:

```python
from __future__ import annotations

from superseded.providers.base import Provider, ProviderConfigError, ProviderResponse


def test_provider_response_defaults():
    r = ProviderResponse(content="hello")
    assert r.content == "hello"
    assert r.prompt_tokens == 0
    assert r.completion_tokens == 0
    assert r.model == ""
    assert r.raw is None


def test_provider_response_is_frozen():
    r = ProviderResponse(content="hello")
    try:
        r.content = "world"  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("ProviderResponse should be frozen")


def test_provider_config_error_is_runtime_error():
    assert issubclass(ProviderConfigError, RuntimeError)


def test_provider_protocol_is_typing_protocol():
    from typing import Protocol as TypingProtocol

    # Provider must be a typing.Protocol so any object with the right shape matches.
    assert isinstance(Provider, type)
    assert issubclass(Provider, TypingProtocol) or Provider._is_protocol  # type: ignore[attr-defined]


def test_provider_protocol_has_complete_method():
    # Structural check: any class with a `complete` method and `name` property satisfies Provider.
    class Fake:
        name = "fake"

        def complete(self, prompt, *, model=None, timeout=600.0, temperature=0.0):
            return ProviderResponse(content="ok")

    fake = Fake()
    assert hasattr(fake, "complete")
    assert hasattr(fake, "name")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_providers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'superseded.providers'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/superseded/providers/__init__.py`:

```python
from __future__ import annotations
```

Create `src/superseded/providers/base.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ProviderResponse:
    """A provider's completion result plus the metadata the engine wants."""

    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    raw: object | None = None


class ProviderConfigError(RuntimeError):
    """A provider is misconfigured (e.g. missing API key)."""


class Provider(Protocol):
    """A direct-API model provider. Replaces the subprocess-shaped `Agent`."""

    @property
    def name(self) -> str: ...

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        timeout: float = 600.0,
        temperature: float = 0.0,
    ) -> ProviderResponse: ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_providers.py -v`
Expected: PASS — all 5 tests.

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check src/superseded/providers/ tests/test_providers.py && uv run ruff format src/superseded/providers/ tests/test_providers.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/superseded/providers/__init__.py src/superseded/providers/base.py tests/test_providers.py
git commit -m "feat(providers): add Provider protocol, ProviderResponse, ProviderConfigError"
```

---

### Task 3: Create `providers/parsing.py` — `_parse_findings_json`

Replaces the JSON-array extraction that lived in `agents/parsing.py`. Same purpose, simpler surface: take the raw model output, return a list of finding dicts (each will get `pass_name` injected).

**Files:**
- Create: `src/superseded/providers/parsing.py`
- Modify: `tests/test_providers.py` (append parsing tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_providers.py`:

```python
import pytest

from superseded.providers.parsing import parse_findings_json


def test_parse_findings_json_bare_array():
    raw = '[{"severity": "critical", "file": "a.py", "line": 1}]'
    items = parse_findings_json(raw, "security")
    assert len(items) == 1
    assert items[0]["severity"] == "critical"
    assert items[0]["pass_name"] == "security"


def test_parse_findings_json_fenced_block():
    raw = 'Here you go:\n```json\n[{"severity": "nit", "file": "a.py", "line": 1}]\n```\n'
    items = parse_findings_json(raw, "style")
    assert len(items) == 1
    assert items[0]["pass_name"] == "style"


def test_parse_findings_json_array_embedded_in_prose():
    raw = (
        "I reviewed the diff and found:\n"
        '[{"severity": "critical", "file": "a.py", "line": 1, "title": "t"}]\n'
        "Let me know if you need more detail."
    )
    items = parse_findings_json(raw, "correctness")
    assert len(items) == 1
    assert items[0]["title"] == "t"


def test_parse_findings_json_empty_array_returns_empty_list():
    assert parse_findings_json("[]", "security") == []


def test_parse_findings_json_no_array_returns_empty_list():
    assert parse_findings_json("no json here", "security") == []


def test_parse_findings_json_malformed_array_returns_empty_list():
    # Truncated/garbled array — return [] so the retry path (engine) can react.
    assert parse_findings_json("[{bad json", "security") == []


def test_parse_findings_json_top_level_dict_rejected():
    # Prompt asks for an array; a single dict is schema drift — return [].
    assert parse_findings_json('{"severity": "critical"}', "security") == []


def test_parse_findings_json_array_element_not_dict_rejected():
    # ["string"] is not a list of finding dicts.
    assert parse_findings_json('["just a string"]', "security") == []


def test_parse_findings_json_injects_pass_name_into_each_item():
    raw = '[{"severity": "critical", "file": "a.py", "line": 1}, {"severity": "nit", "file": "b.py", "line": 2}]'
    items = parse_findings_json(raw, "architecture")
    assert all(i["pass_name"] == "architecture" for i in items)
    assert len(items) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_providers.py -v -k parse_findings_json`
Expected: FAIL — `ModuleNotFoundError: No module named 'superseded.providers.parsing'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/superseded/providers/parsing.py`:

```python
from __future__ import annotations

import json
import re

_FENCED_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)
_ARRAY_RE = re.compile(r"\[\s*\{.*\}\s*\]", re.DOTALL)


def parse_findings_json(raw: str, pass_name: str) -> list[dict]:
    """Extract a JSON array of finding dicts from `raw` model output.

    Handles three common shapes: a bare JSON array, a ```json fenced block,
    and an array embedded in prose. Returns [] for anything else so the
    engine's retry path can react to schema drift. Each returned dict gets
    `pass_name` injected.
    """
    candidate = _extract_array_text(raw)
    if candidate is None:
        return []
    try:
        parsed = json.loads(candidate)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    items: list[dict] = []
    for el in parsed:
        if isinstance(el, dict):
            el["pass_name"] = pass_name
            items.append(el)
    return items


def _extract_array_text(raw: str) -> str | None:
    # Try fenced ```json block first (most explicit).
    m = _FENCED_RE.search(raw)
    if m:
        inner = m.group(1).strip()
        if inner.startswith("["):
            return inner
    # Try bare/whitespace-trimmed JSON array.
    stripped = raw.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        return stripped
    # Fall back to an array embedded in prose.
    m = _ARRAY_RE.search(raw)
    if m:
        return m.group(0)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_providers.py -v -k parse_findings_json`
Expected: PASS — all 9 parsing tests.

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check src/superseded/providers/parsing.py tests/test_providers.py && uv run ruff format src/superseded/providers/parsing.py tests/test_providers.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/superseded/providers/parsing.py tests/test_providers.py
git commit -m "feat(providers): add parse_findings_json helper"
```

---

### Task 4: Create `providers/deepseek.py` — `DeepSeekProvider`

**Files:**
- Create: `src/superseded/providers/deepseek.py`
- Modify: `tests/test_providers.py` (append DeepSeek tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_providers.py`:

```python
from superseded.providers.deepseek import (
    DEEPSEEK_API_KEY_ENV,
    DEEPSEEK_DEFAULT_BASE_URL,
    DEEPSEEK_DEFAULT_MODEL,
    DeepSeekProvider,
)


def _fake_completion(*, content="[]", prompt_tokens=10, completion_tokens=5, model="deepseek-v4-flash"):
    """Build an object that quacks like openai's ChatCompletion."""
    message = type("Msg", (), {"content": content, "reasoning_content": None})()
    choice = type("Choice", (), {"message": message})()
    usage = type("Usage", (), {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens})()
    return type(
        "Resp",
        (),
        {"choices": [choice], "usage": usage, "model": model},
    )()


def test_deepseek_constants():
    assert DEEPSEEK_API_KEY_ENV == "SUPERSEDED_DEEPSEEK_API_KEY"
    assert DEEPSEEK_DEFAULT_BASE_URL == "https://api.deepseek.com"
    assert DEEPSEEK_DEFAULT_MODEL == "deepseek-v4-flash"


def test_deepseek_provider_name():
    p = DeepSeekProvider(api_key="sk-test")
    assert p.name == "deepseek"


def test_deepseek_provider_uses_env_when_no_arg(monkeypatch):
    monkeypatch.setenv(DEEPSEEK_API_KEY_ENV, "sk-from-env")
    p = DeepSeekProvider()
    assert p.name == "deepseek"  # construction succeeded


def test_deepseek_provider_raises_when_no_key(monkeypatch):
    monkeypatch.delenv(DEEPSEEK_API_KEY_ENV, raising=False)
    with pytest.raises(ProviderConfigError, match="No DeepSeek API key"):
        DeepSeekProvider()


def test_deepseek_complete_returns_content(monkeypatch):
    p = DeepSeekProvider(api_key="sk-test")
    captured = {}

    class FakeClient:
        def __init__(self, **kw):
            captured["init_kwargs"] = kw

        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **kw):
            captured["create_kwargs"] = kw
            return _fake_completion(content='[{"severity": "critical"}]', prompt_tokens=42, completion_tokens=7)

    monkeypatch.setattr("superseded.providers.deepseek.OpenAI", FakeClient)
    resp = p.complete("the prompt", model="deepseek-v4-flash", timeout=120.0)
    assert resp.content == '[{"severity": "critical"}]'
    assert resp.prompt_tokens == 42
    assert resp.completion_tokens == 7
    assert resp.model == "deepseek-v4-flash"
    # The prompt was forwarded as the user message.
    assert captured["create_kwargs"]["messages"] == [{"role": "user", "content": "the prompt"}]
    assert captured["create_kwargs"]["timeout"] == 120.0
    assert captured["create_kwargs"]["model"] == "deepseek-v4-flash"


def test_deepseek_complete_uses_default_model_when_none(monkeypatch):
    p = DeepSeekProvider(api_key="sk-test")

    class FakeClient:
        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **kw):
            assert kw["model"] == DEEPSEEK_DEFAULT_MODEL
            return _fake_completion()

    monkeypatch.setattr("superseded.providers.deepseek.OpenAI", FakeClient)
    p.complete("p")


def test_deepseek_complete_ignores_reasoning_content(monkeypatch):
    """Reasoner models populate both .reasoning_content and .content; we use .content only."""
    p = DeepSeekProvider(api_key="sk-test")

    class FakeClient:
        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **kw):
            message = type(
                "Msg",
                (),
                {"content": '[]', "reasoning_content": "let me think..."},
            )()
            choice = type("Choice", (), {"message": message})()
            usage = type("Usage", (), {"prompt_tokens": 1, "completion_tokens": 1})()
            return type("Resp", (), {"choices": [choice], "usage": usage, "model": "x"})

    monkeypatch.setattr("superseded.providers.deepseek.OpenAI", FakeClient)
    resp = p.complete("p")
    assert resp.content == "[]"


def test_deepseek_complete_handles_null_content(monkeypatch):
    """A refusal may return content=None; provider should normalise to empty string."""
    p = DeepSeekProvider(api_key="sk-test")

    class FakeClient:
        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **kw):
            message = type("Msg", (), {"content": None, "reasoning_content": None})()
            choice = type("Choice", (), {"message": message})()
            usage = type("Usage", (), {"prompt_tokens": 0, "completion_tokens": 0})()
            return type("Resp", (), {"choices": [choice], "usage": usage, "model": "x"})

    monkeypatch.setattr("superseded.providers.deepseek.OpenAI", FakeClient)
    resp = p.complete("p")
    assert resp.content == ""


def test_deepseek_init_forwards_base_url_and_retries(monkeypatch):
    """The OpenAI client must be configured with max_retries and the DeepSeek base_url."""
    captured = {}

    class FakeClient:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr("superseded.providers.deepseek.OpenAI", FakeClient)
    DeepSeekProvider(api_key="sk-test")
    assert captured["base_url"] == DEEPSEEK_DEFAULT_BASE_URL
    assert captured["max_retries"] == 2
    assert captured["api_key"] == "sk-test"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_providers.py -v -k deepseek`
Expected: FAIL — `ModuleNotFoundError: No module named 'superseded.providers.deepseek'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/superseded/providers/deepseek.py`:

```python
from __future__ import annotations

import os

from openai import OpenAI

from superseded.providers.base import ProviderConfigError, ProviderResponse

DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"
DEEPSEEK_API_KEY_ENV = "SUPERSEDED_DEEPSEEK_API_KEY"


class DeepSeekProvider:
    """Direct DeepSeek Chat Completions client (OpenAI-compatible)."""

    name = "deepseek"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEEPSEEK_DEFAULT_BASE_URL,
        default_model: str = DEEPSEEK_DEFAULT_MODEL,
    ) -> None:
        resolved = api_key or os.environ.get(DEEPSEEK_API_KEY_ENV)
        if not resolved:
            raise ProviderConfigError(
                f"No DeepSeek API key. Set ${DEEPSEEK_API_KEY_ENV} or pass api_key=."
            )
        self._client = OpenAI(api_key=resolved, base_url=base_url, max_retries=2)
        self._default_model = default_model

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        timeout: float = 600.0,
        temperature: float = 0.0,
    ) -> ProviderResponse:
        resolved = model or self._default_model
        resp = self._client.chat.completions.create(
            model=resolved,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            timeout=timeout,
        )
        # DeepSeek reasoner models populate both .reasoning_content (CoT) and
        # .content. We always read .content — the JSON we want is there
        # regardless of model, so the same code path handles chat and reasoner.
        message = resp.choices[0].message
        content = getattr(message, "content", None) or ""
        usage = getattr(resp, "usage", None)
        return ProviderResponse(
            content=content,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            model=getattr(resp, "model", resolved),
            raw=resp,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_providers.py -v`
Expected: PASS — all provider tests (base + parsing + deepseek).

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check src/superseded/providers/ tests/test_providers.py && uv run ruff format src/superseded/providers/ tests/test_providers.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/superseded/providers/deepseek.py tests/test_providers.py
git commit -m "feat(providers): add DeepSeekProvider with OpenAI-compatible client"
```

---

### Task 5: Wire `providers/__init__.py` exports + add `ReviewUsage` model

**Files:**
- Modify: `src/superseded/providers/__init__.py`
- Modify: `src/superseded/models.py`
- Modify: `tests/test_providers.py` (append PROVIDER_MAP test)
- Modify: `tests/test_models.py` (append ReviewUsage test)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_providers.py`:

```python
def test_provider_map_exports():
    from superseded.providers import PROVIDER_MAP, DeepSeekProvider, Provider, ProviderConfigError, ProviderResponse

    assert "deepseek" in PROVIDER_MAP
    assert PROVIDER_MAP["deepseek"] is DeepSeekProvider
```

Append to `tests/test_models.py`:

```python
def test_review_usage_defaults():
    from superseded.models import ReviewUsage

    u = ReviewUsage()
    assert u.prompt_tokens == 0
    assert u.completion_tokens == 0
    assert u.per_pass == {}


def test_review_usage_per_pass_dict():
    from superseded.models import ReviewUsage

    u = ReviewUsage(prompt_tokens=100, completion_tokens=50, per_pass={"security": (60, 30)})
    assert u.per_pass["security"] == (60, 30)


def test_review_result_has_usage_field():
    from superseded.models import ReviewResult, ReviewUsage

    r = ReviewResult()
    assert isinstance(r.usage, ReviewUsage)
    assert r.usage.prompt_tokens == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_providers.py tests/test_models.py -v -k "provider_map or review_usage or review_result_has_usage"`
Expected: FAIL — `ImportError: cannot import name 'PROVIDER_MAP'` and `AttributeError: ReviewResult has no field usage`.

- [ ] **Step 3: Write minimal implementation**

Edit `src/superseded/providers/__init__.py`:

```python
from __future__ import annotations

from superseded.providers.base import Provider, ProviderConfigError, ProviderResponse
from superseded.providers.deepseek import DeepSeekProvider

PROVIDER_MAP: dict[str, type[Provider]] = {
    "deepseek": DeepSeekProvider,
}

__all__ = [
    "PROVIDER_MAP",
    "DeepSeekProvider",
    "Provider",
    "ProviderConfigError",
    "ProviderResponse",
]
```

For `src/superseded/models.py`: read the file first to understand the existing `ReviewResult` shape, then add the new `ReviewUsage` model and the `usage` field on `ReviewResult`. The minimal change is:

```python
class ReviewUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    per_pass: dict[str, tuple[int, int]] = {}
```

Add to `ReviewResult` (matching the existing `Field(default_factory=list)` pattern used at models.py:56-58):

```python
class ReviewResult(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    dropped_findings: list[Finding] = Field(default_factory=list)
    usage: ReviewUsage = Field(default_factory=ReviewUsage)

    # ... existing summary property unchanged ...
```

`dict[str, tuple[int, int]]` needs a `default_factory=dict` if Pydantic v2 flags the bare `{}` default; the existing `Finding` model uses bare defaults for some fields so this is consistent — but if the test fails on that field, switch to `Field(default_factory=dict)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_providers.py tests/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite to catch any regressions**

Run: `uv run pytest tests/ -q`
Expected: full suite passes (no callers depend on the new field yet, so this should be safe).

- [ ] **Step 6: Lint and format**

Run: `uv run ruff check src/superseded/providers/ src/superseded/models.py tests/test_providers.py tests/test_models.py && uv run ruff format src/superseded/providers/ src/superseded/models.py tests/test_providers.py tests/test_models.py`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/superseded/providers/__init__.py src/superseded/models.py tests/test_providers.py tests/test_models.py
git commit -m "feat(providers): export PROVIDER_MAP; add ReviewUsage model"
```

---

### Task 6: Refactor `ReviewEngine` to use `Provider` and wire all callers

This is the central refactor. Each step is small; only commit at the end when the full suite is green. The repo will be temporarily broken between steps — that is fine, we commit only when green.

**Files:**
- Modify: `src/superseded/review/engine.py`
- Modify: `src/superseded/config.py`
- Modify: `src/superseded/cli.py`
- Modify: `src/superseded/server/config.py`
- Modify: `src/superseded/server/worker.py`
- Modify: `tests/test_engine.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_server_worker.py`
- Modify: `tests/test_server_config.py`
- Modify: `tests/test_integration.py`

- [ ] **Step 1: Read current state of files you're about to modify**

Read these files end-to-end before editing so you understand the surrounding code you'll be touching:

```
src/superseded/review/engine.py
src/superseded/config.py
src/superseded/cli.py            (lines 89-150 for resolve_*, lines 273-540 for review, lines 698-780 for init)
src/superseded/server/config.py
src/superseded/server/worker.py  (lines 50-150 for SandboxSettings; lines 480-530 for sandbox branch)
tests/test_engine.py
tests/test_cli.py
tests/test_server_worker.py
tests/test_integration.py
```

- [ ] **Step 2: Rewrite `src/superseded/review/engine.py`**

Replace the file's contents with this version (the diff against the current `engine.py` is: drop `Agent`/`Session`/`AgentExecutor`/`SubprocessExecutor` imports; add `Provider`/`PROVIDER_MAP`/`ProviderResponse`/`parse_findings_json` imports; rename `agent` → `provider` everywhere; `_run_and_validate` calls `self.provider.complete()` + `parse_findings_json()`; `run_pass` drops `sess`; `review()` drops `cwd`/`env`/`executor`; `_run_verification` drops `sess`; accumulate token usage into `result.usage`):

```python
from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING

from superseded.models import Finding, ReviewResult, ReviewUsage
from superseded.providers import PROVIDER_MAP, Provider, ProviderResponse
from superseded.providers.parsing import parse_findings_json
from superseded.review.merger import merge_findings
from superseded.review.prompts import build_prompt, build_retry_prompt
from superseded.review.verifier import _parse_verdicts

if TYPE_CHECKING:
    from superseded.config import Config

logger = logging.getLogger(__name__)

DEFAULT_PASS_TIMEOUT = 600

ProgressFn = Callable[[str, str], None]


class ReviewEngine:
    def __init__(self, provider: Provider, config: Config) -> None:
        self.provider = provider
        self.model: str | None = None
        self.config = config

    @classmethod
    def select(
        cls, provider_name: str, model: str | None, config: Config | None = None
    ) -> ReviewEngine:
        provider_cls = PROVIDER_MAP.get(provider_name)
        if provider_cls is None:
            raise ValueError(
                f"Unknown provider: {provider_name}. Choose from: {list(PROVIDER_MAP)}"
            )
        provider = provider_cls()
        engine = cls(provider=provider, config=config or Config())
        engine.model = model
        return engine

    def run_pass(
        self,
        pass_name: str,
        prompt: str,
        timeout: int = DEFAULT_PASS_TIMEOUT,
        progress: ProgressFn | None = None,
    ) -> tuple[list[Finding], ReviewUsage]:
        if progress is not None:
            progress(pass_name, "start")
        findings, errors, usage = self._run_and_validate(pass_name, prompt, timeout)
        if errors:
            logger.info("Retrying pass %s: %d finding(s) failed validation", pass_name, len(errors))
            retried, _, _ = self._run_and_validate(
                pass_name, build_retry_prompt(prompt, errors), timeout
            )
            if retried:
                findings = retried
        if progress is not None:
            progress(pass_name, "done")
        return findings, usage

    def _run_and_validate(
        self, pass_name: str, prompt: str, timeout: int
    ) -> tuple[list[Finding], list[str], ReviewUsage]:
        resp = self.provider.complete(prompt, model=self.model, timeout=timeout)
        raw_findings = parse_findings_json(resp.content, pass_name)
        findings: list[Finding] = []
        errors: list[str] = []
        for item in raw_findings:
            try:
                findings.append(Finding(**item))
            except Exception as err:
                errors.append(str(err))
                logger.warning("Skipping malformed finding item in pass %s: %s", pass_name, err)
        usage = ReviewUsage(
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            per_pass={pass_name: (resp.prompt_tokens, resp.completion_tokens)},
        )
        return findings, errors, usage

    def _run_verification(
        self,
        result: ReviewResult,
        diff: str,
        file_context: str | None,
        timeout: int,
    ) -> ReviewResult:
        """Run a post-merge verification pass over the deduplicated findings."""
        from superseded.review.prompts import build_verify_prompt

        if not result.findings:
            return result

        prompt = build_verify_prompt(result.findings, diff, file_context)
        try:
            resp = self.provider.complete(prompt, model=self.model, timeout=timeout)
        except Exception as err:
            logger.warning("Verification pass failed: %s", err)
            result.warnings.append(f"Verification pass failed: {err}")
            return result

        errors, verdicts = _parse_verdicts(resp.content, collect_errors=True)

        kept: list[Finding] = []
        dropped_findings: list[Finding] = []
        dropped_count = 0
        reestimated_count = 0

        for f in result.findings:
            verdict = verdicts.get(f.id)
            if verdict is None:
                f.verification = "kept"
                kept.append(f)
                continue
            if verdict.action == "drop":
                f.verification = "dropped"
                f.verification_reason = verdict.reason
                dropped_count += 1
                dropped_findings.append(f)
                continue
            f.verification = "kept"
            if verdict.severity is not None:
                f.severity = verdict.severity
                f.verified_severity = verdict.severity
                reestimated_count += 1
            if verdict.confidence is not None:
                f.confidence = verdict.confidence
            if verdict.reason:
                f.verification_reason = verdict.reason
            kept.append(f)

        for err in errors:
            logger.warning("Verification parse error: %s", err)

        dropped_msg = f"Verification completed: {dropped_count} findings dropped, {len(kept)} kept"
        if reestimated_count:
            dropped_msg += f" ({reestimated_count} re-estimated)"
        result.warnings.append(dropped_msg)

        # Accumulate verify-pass tokens into result.usage too.
        result.usage.prompt_tokens += resp.prompt_tokens
        result.usage.completion_tokens += resp.completion_tokens
        result.usage.per_pass["verify"] = (resp.prompt_tokens, resp.completion_tokens)

        return ReviewResult(
            findings=kept,
            warnings=result.warnings,
            dropped_findings=dropped_findings,
            usage=result.usage,
        )

    def review(
        self,
        diff: str,
        pr_description: str | None = None,
        file_context: str | None = None,
        memory_context: str | None = None,
        static_signals: str | None = None,
        usage_signals: str | None = None,
        conventions_signals: str | None = None,
        spec_signals: str | None = None,
        learned_context: str | None = None,
        passes: list[str] | None = None,
        timeout: int = DEFAULT_PASS_TIMEOUT,
        progress: ProgressFn | None = None,
    ) -> ReviewResult:
        if passes is None or len(passes) == 0:
            passes = [
                n
                for n in ["security", "correctness", "performance", "style", "architecture"]
                if self.config.is_pass_enabled(n)
            ]

        all_findings: list[list[Finding]] = []
        warnings: list[str] = []
        total_usage = ReviewUsage()

        with ThreadPoolExecutor(max_workers=max(1, len(passes))) as pool:
            future_to_pass = {}
            for pass_name in passes:
                prompt = build_prompt(
                    pass_name=pass_name,
                    diff=diff,
                    pr_description=pr_description,
                    file_context=file_context,
                    memory_context=memory_context,
                    static_signals=static_signals,
                    usage_signals=usage_signals,
                    conventions_signals=conventions_signals,
                    spec_signals=spec_signals,
                    learned_context=learned_context,
                )
                future = pool.submit(self.run_pass, pass_name, prompt, timeout, progress)
                future_to_pass[future] = pass_name

            for future in as_completed(future_to_pass):
                pass_name = future_to_pass[future]
                try:
                    findings, usage = future.result()
                    all_findings.append(findings)
                    total_usage.prompt_tokens += usage.prompt_tokens
                    total_usage.completion_tokens += usage.completion_tokens
                    total_usage.per_pass.update(usage.per_pass)
                except Exception as err:
                    msg = f"Review pass '{pass_name}' failed and was skipped: {err}"
                    logger.warning(msg)
                    warnings.append(msg)
                    if progress is not None:
                        progress(pass_name, "failed")

            result = self.merge_findings(all_findings)
            result.warnings = warnings
            result.usage = total_usage

            if self.config.verify and result.findings:
                result = self._run_verification(result, diff, file_context, timeout)

        return result

    def merge_findings(self, finding_groups: list[list[Finding]]) -> ReviewResult:
        return merge_findings(finding_groups)
```

- [ ] **Step 3: Update `src/superseded/config.py`**

Rename `agent: str = "opencode"` to `provider: str = "deepseek"`. Remove `sandbox: bool = False`. The minimal diff:

```python
class Config(BaseModel):
    provider: str = "deepseek"
    model: str | None = None
    passes: PassConfig = PassConfig()
    post_to_pr: bool = False
    format: str = "table"
    log_format: str = "text"
    log_level: str = "WARNING"
    memory: bool = True
    static_analysis: bool = True
    usage_retrieval: bool = True
    conventions: bool = True
    spec_retrieval: bool = True
    graph: bool = True
    progressive: bool = True
    learned_review: bool = True
    reflection_threshold: int = 5
    max_learned_rules: int = 5
    verify: bool = True

    def is_pass_enabled(self, name: str) -> bool:
        return getattr(self.passes, name, False)
```

(Backward-compat handling for legacy `agent:` and `sandbox:` YAML keys comes in Task 8 — for now just rename the field. Existing tests for `Config` may need their `agent=` construction updated to `provider=`.)

- [ ] **Step 4: Update `src/superseded/server/config.py`**

Remove these fields from `ServerConfig`: `sandbox_enabled`, `sandbox_timeout`, `sandbox_keep_on_error`, `sandbox_io_mode`, `sandbox_kind`, `smolvm_binary`, `smolvm_image`, `smolvm_image_claude`, `smolvm_image_opencode`, `smolvm_image_codex`. Add `deepseek_api_key: str | None = None`.

Remove the corresponding env-loader blocks in `ServerConfig.from_env()` (the `SUPERSEDED_SANDBOX*`, `SUPERSEDED_SMOLVM*` reads). Add a single new env read:

```python
deepseek_api_key = os.environ.get("SUPERSEDED_DEEPSEEK_API_KEY")
if deepseek_api_key:
    kwargs["deepseek_api_key"] = deepseek_api_key
```

- [ ] **Step 5: Update `src/superseded/server/worker.py`**

This file has the most surgical changes. Make these edits:

(a) Delete the `SandboxSettings` dataclass (worker.py:~56-70) entirely.

(b) Delete the `_agent_smolvm_image` (worker.py:~78-87) and `_sandbox_unavailable_msg` (worker.py:~89-100) helpers.

(c) Update `ReviewWorker.__init__` (worker.py:~111-134):
   - Drop the `sandbox: SandboxSettings | None = None` parameter.
   - Add `provider: Provider | None = None` parameter.
   - Rename `server_agent: str | None = None` → `server_provider: str | None = None`.
   - Replace `self._sandbox = sandbox` with `self._provider = provider`. (Caller is required to pass a provider; if `None`, raise `ValueError("ReviewWorker requires a Provider")` — don't paper over a misconfiguration.)
   - Replace `self.server_agent = server_agent` with `self.server_provider = server_provider`.

(d) Update the `ReviewWorker` review method that calls into the engine (worker.py:~240-260):
   - Replace `server_agent=self.server_agent` with `server_provider=self.server_provider`.
   - Replace `sandbox=self._sandbox` with `provider=self._provider`.

(e) Update the helper function that builds a Config from server args (worker.py:~311, ~357-358):
   - Rename `server_agent` parameter to `server_provider`.
   - Change `config.agent = server_agent` to `config.provider = server_provider`.

(f) Update the second helper (worker.py:~371-402) the same way: rename `server_agent` to `server_provider` in both the function signature and the call-through.

(g) Replace the sandbox-branch in the review path (worker.py:~495-525) with a single engine construction:

```python
    engine = ReviewEngine(provider=self._provider, config=config)
    result = engine.review(
        diff=diff,
        pr_description=pr_description,
        file_context=file_context,
        memory_context=memory_context,
        static_signals=static_signals,
        usage_signals=usage_signals,
        conventions_signals=conventions_signals,
        spec_signals=spec_signals,
        learned_context=learned_context,
        passes=passes,
        timeout=timeout,
        progress=progress,
    )
```

Note: drop the `cwd=`, `env=`, and `executor=` keyword arguments that previously went to `engine.review(...)` — they no longer exist on the new `review()` signature.

Add the necessary imports at the top of the file: `from superseded.providers import DeepSeekProvider, Provider` and `from superseded.review.engine import ReviewEngine` (the latter likely already imported at worker.py:19).

- [ ] **Step 6: Update `src/superseded/cli.py`**

This is a multi-edit step. Make these changes:

(a) Replace imports at the top of the file: drop `SubprocessExecutor`, `AgentExecutor`, `make_sandbox_executor` from `superseded.review.executor` (the whole import line goes since the module will be deleted in Task 7). Drop `SKILL_AGENTS`, `build_skill_text`, `install_skill` from `superseded.skill`. Add `from superseded.providers import PROVIDER_MAP, DeepSeekProvider`. Drop `detect_agents`, `pick_agent`, `default_model_for`, `detect_gh`, `detect_code_review_graph` from `superseded.detection` (the module gets deleted). Keep only `detect_gh` and `detect_code_review_graph` by inlining them — see step (e).

(b) Rename `resolve_agent` → `resolve_provider`:

```python
PROVIDER_ENV = "SUPERSEDED_PROVIDER"


def resolve_provider(provider_flag: str | None, config: Config) -> str:
    if provider_flag is not None:
        return provider_flag
    return config.provider
```

In the same edit, update the `review` command's `@click.option("--agent", ...)` to `@click.option("--provider", default=None, help="Model provider (default: deepseek)")`, and rename the function parameter from `agent` to `provider`. Update the call to `_run_review(...)` to pass `provider=provider` instead of `agent=agent`.

(c) Add the `SUPERSEDED_AGENT` backward-compat alias: at the top of `resolve_provider`, before the flag check:

```python
legacy = os.environ.get("SUPERSEDED_AGENT")
if legacy and not os.environ.get(PROVIDER_ENV):
    import warnings

    warnings.warn(
        "SUPERSEDED_AGENT is deprecated; use SUPERSEDED_PROVIDER.",
        DeprecationWarning,
        stacklevel=2,
    )
    return legacy
```

(d) Delete the `resolve_sandbox`, `_resolve_smolvm_image`, and `_select_executor` functions (cli.py:106-150). Delete the `--sandbox/--no-sandbox` option (cli.py:326-331). Delete the `sandbox` parameter from `review()` and from `_run_review()`. Replace `_select_executor(...)` call site (cli.py:452) and the surrounding sandbox branch (cli.py:454-470) with a single `ReviewEngine.select(provider, model, config)` call.

(e) Replace the `serve` command's sandbox wiring block (cli.py:~985-1042). The actual diff:

- Drop `SandboxSettings` from the `from superseded.server.worker import ...` line (cli.py:975). Add `from superseded.providers import DeepSeekProvider`.
- Delete the entire "refusing to serve without a sandbox" block at cli.py:985-1007.
- Delete the `sandbox = SandboxSettings(...)` construction at cli.py:1021-1033.
- Change the `ReviewWorker(...)` construction (cli.py:1034-1042) to:

```python
    worker = ReviewWorker(
        github=github,
        repo_manager=repo_manager,
        max_concurrent=config.max_concurrent_reviews,
        store=store,
        server_provider=config.provider,
        server_model=config.model,
        provider=DeepSeekProvider(api_key=config.deepseek_api_key),
    )
```

- Before calling `config.require_configured()`, add an explicit DeepSeek-key check so the failure mode is clear (replace the deleted sandbox-refusal block with this):

```python
    if not config.deepseek_api_key:
        click.echo(
            "Error: SUPERSEDED_DEEPSEEK_API_KEY must be set to serve.",
            err=True,
        )
        sys.exit(2)
```

- Change `config.agent` to `config.provider` at the worker construction site (line 1039 in the original).
- Leave the rest of `serve` (uvicorn / app construction / lifecycle / TLS) unchanged.

(f) Delete the entire `skill` command group (cli.py:781-835) — the `@cli.group() def skill`, `@skill.command("install")`, `@skill.command("print")`, and `_run_skill_install`. The `skill.py` module itself gets deleted in Task 7.

(g) Replace `_run_init` (cli.py:725-779) with a simplified version:

```python
def _run_init(force: bool, config_path: Path | None) -> None:
    target = config_path or Path(".superseded.yaml")

    if target.exists() and not force:
        _status(f"Error: {target} already exists. Use --force to overwrite.")
        sys.exit(2)

    if shutil.which("gh") is not None:
        _status("gh CLI: found")
    else:
        _status("gh CLI: not found (PR features will be disabled)")

    if (Path.cwd() / ".code-review-graph").is_dir():
        try:
            import code_review_graph  # noqa: F401
            _status("code-review-graph: found")
        except ImportError:
            _status("code-review-graph: graph dir present but package not installed")
    else:
        _status(
            "code-review-graph: not installed "
            "(graph-grounded reviews disabled; install with: "
            "uv add code-review-graph && code-review-graph build)"
        )

    if os.environ.get("SUPERSEDED_DEEPSEEK_API_KEY"):
        _status("SUPERSEDED_DEEPSEEK_API_KEY: set")
    else:
        _status(
            "SUPERSEDED_DEEPSEEK_API_KEY: not set — set it before running `superseded review`."
        )

    cfg = Config(provider="deepseek")
    write_config(cfg, target)
    _status(f"Wrote {target} (provider: deepseek)")
```

Update the `init` command's `@click.option` to drop `--agent` (no longer needed). Update the function signature to drop the `agent_override` parameter. Inline the imports this needs at the top of the function (`shutil`, `os`, `Path`, `Config`, `write_config`) — most are already imported.

- [ ] **Step 7: Port `tests/test_engine.py`**

This is the largest test edit. Replace the file's contents:

```python
from __future__ import annotations

from unittest.mock import MagicMock

from superseded.models import Finding, ReviewResult
from superseded.providers import ProviderResponse
from superseded.providers.base import Provider
from superseded.review.engine import ReviewEngine


def make_finding(
    pass_name="security", severity="critical", file="a.py", line=1, title="test issue"
):
    return Finding(
        pass_name=pass_name,
        severity=severity,
        file=file,
        line=line,
        end_line=line + 1,
        title=title,
        description="desc",
        suggestion="fix",
    )


class FakeProvider:
    """A test double matching the Provider protocol."""

    name = "fake"

    def __init__(self, content_by_prompt: dict[str, str] | None = None, default="[]"):
        self._by_prompt = content_by_prompt or {}
        self._default = default
        self.calls: list[str] = []

    def complete(self, prompt, *, model=None, timeout=600.0, temperature=0.0):
        self.calls.append(prompt)
        content = self._by_prompt.get(prompt, self._default)
        return ProviderResponse(content=content, prompt_tokens=10, completion_tokens=5, model="fake")


def test_engine_deduplicates():
    f1 = make_finding()
    f2 = make_finding()
    engine = ReviewEngine(provider=MagicMock(), config=MagicMock())
    result = engine.merge_findings([[f1], [f2]])
    assert len(result.findings) == 1


def test_engine_deduplicates_across_passes():
    security = make_finding(pass_name="security", file="a.py", line=10, title="same bug")
    correctness = make_finding(pass_name="correctness", file="a.py", line=10, title="same bug")
    engine = ReviewEngine(provider=MagicMock(), config=MagicMock())
    result = engine.merge_findings([[security], [correctness]])
    assert len(result.findings) == 1
    assert result.findings[0].file == "a.py"
    assert result.findings[0].line == 10
    assert result.findings[0].title == "same bug"


def test_engine_dedup_keeps_highest_severity():
    low = make_finding(severity="suggestion", file="a.py", line=10, title="same bug")
    high = make_finding(severity="critical", file="a.py", line=10, title="same bug")
    engine = ReviewEngine(provider=MagicMock(), config=MagicMock())
    result = engine.merge_findings([[low], [high]])
    assert len(result.findings) == 1
    assert result.findings[0].severity == "critical"


def test_engine_dedup_keeps_highest_severity_regardless_of_order():
    high = make_finding(severity="critical", file="a.py", line=10, title="same bug")
    low = make_finding(severity="suggestion", file="a.py", line=10, title="same bug")
    engine = ReviewEngine(provider=MagicMock(), config=MagicMock())
    result = engine.merge_findings([[high], [low]])
    assert len(result.findings) == 1
    assert result.findings[0].severity == "critical"


def test_engine_sorts_by_severity():
    f1 = make_finding(severity="nit", line=1)
    f2 = make_finding(severity="critical", line=2)
    f3 = make_finding(severity="suggestion", line=3)
    engine = ReviewEngine(provider=MagicMock(), config=MagicMock())
    result = engine.merge_findings([[f1, f2, f3]])
    assert result.findings[0].severity == "critical"
    assert result.findings[-1].severity == "nit"


def test_engine_selects_provider():
    from superseded.providers import DeepSeekProvider

    engine = ReviewEngine.select("deepseek", model=None)
    assert isinstance(engine.provider, DeepSeekProvider)


def test_engine_select_rejects_unknown_provider():
    import pytest

    with pytest.raises(ValueError, match="Unknown provider"):
        ReviewEngine.select("bogus", model=None)


def test_review_continues_when_one_pass_fails():
    engine = ReviewEngine(provider=MagicMock(), config=MagicMock())
    engine.config.is_pass_enabled = lambda name: True

    good_finding = make_finding(severity="critical", line=5)

    def fake_run_pass(pass_name, prompt, timeout=300, progress=None):
        if pass_name == "correctness":
            raise RuntimeError("boom")
        return [good_finding], ReviewUsage()

    engine.run_pass = fake_run_pass  # type: ignore[method-assign]
    result = engine.review(diff="diff", passes=["security", "correctness"])
    assert isinstance(result, ReviewResult)
    assert len(result.findings) == 1
    assert result.findings[0] is good_finding


def test_run_pass_skips_and_logs_malformed_findings(caplog):
    import logging

    engine = ReviewEngine(provider=MagicMock(), config=MagicMock())
    raw_items = [
        '{"severity": "critical", "file": "a.py", "line": 1, "end_line": 2, "title": "t", "description": "d", "suggestion": "s"}',
        '{"severity": "not-a-severity", "file": "b.py", "line": 1, "end_line": 1, "title": "bad", "description": "d", "suggestion": "s"}',
    ]
    # First call returns one valid + one malformed; retry returns nothing valid.
    engine.provider.complete = MagicMock(
        side_effect=[
            ProviderResponse(content="[" + raw_items[0] + ", " + raw_items[1] + "]"),
            ProviderResponse(content="[" + raw_items[0] + "]"),
        ]
    )
    with caplog.at_level(logging.WARNING, logger="superseded.review.engine"):
        findings, _ = engine.run_pass("security", "prompt")
    assert len(findings) == 1
    assert findings[0].file == "a.py"
    assert "malformed" in caplog.text.lower() or "not-a-severity" in caplog.text
    assert engine.provider.complete.call_count == 2
    second_prompt = engine.provider.complete.call_args_list[1].args[0]
    assert "Correction" in second_prompt


def test_run_pass_retries_once_on_malformed_and_recovers():
    engine = ReviewEngine(provider=MagicMock(), config=MagicMock())
    valid = '{"severity": "critical", "file": "a.py", "line": 1, "end_line": 2, "title": "ok", "description": "d", "suggestion": "s"}'
    bad = '{"severity": "minor", "file": "b.py", "line": 1, "end_line": 1, "title": "bad", "description": "d", "suggestion": "s"}'
    recovered = '{"severity": "important", "file": "b.py", "line": 1, "end_line": 1, "title": "fixed", "description": "d", "suggestion": "s"}'
    engine.provider.complete = MagicMock(
        side_effect=[
            ProviderResponse(content="[" + valid + ", " + bad + "]"),
            ProviderResponse(content="[" + recovered + "]"),
        ]
    )
    findings, _ = engine.run_pass("security", "prompt")
    assert engine.provider.complete.call_count == 2
    assert len(findings) == 1
    assert findings[0].title == "fixed"
    second_prompt = engine.provider.complete.call_args_list[1].args[0]
    assert "Correction" in second_prompt or "correction" in second_prompt


def test_run_pass_retries_at_most_once():
    engine = ReviewEngine(provider=MagicMock(), config=MagicMock())
    valid = '{"severity": "critical", "file": "a.py", "line": 1, "end_line": 2, "title": "ok", "description": "d", "suggestion": "s"}'
    bad = '{"severity": "minor", "file": "b.py", "line": 1, "end_line": 1, "title": "bad", "description": "d", "suggestion": "s"}'
    engine.provider.complete = MagicMock(
        return_value=ProviderResponse(content="[" + valid + ", " + bad + "]")
    )
    engine.run_pass("security", "prompt")
    assert engine.provider.complete.call_count == 2


def test_run_pass_does_not_retry_when_all_valid():
    engine = ReviewEngine(provider=MagicMock(), config=MagicMock())
    valid = '{"severity": "critical", "file": "a.py", "line": 1, "end_line": 2, "title": "ok", "description": "d", "suggestion": "s"}'
    engine.provider.complete = MagicMock(
        return_value=ProviderResponse(content="[" + valid + "]")
    )
    engine.run_pass("security", "prompt")
    assert engine.provider.complete.call_count == 1


def test_review_accumulates_usage():
    engine = ReviewEngine(provider=FakeProvider(default="[]"), config=MagicMock(is_pass_enabled=lambda n: True))
    result = engine.review(diff="d", passes=["security", "correctness"])
    # Two passes, each FakeProvider call returns prompt_tokens=10, completion_tokens=5.
    assert result.usage.prompt_tokens == 20
    assert result.usage.completion_tokens == 10
    assert set(result.usage.per_pass.keys()) == {"security", "correctness"}


def test_run_verification_keeps_all():
    from superseded.config import Config

    engine = ReviewEngine(provider=MagicMock(), config=Config(verify=True))
    f1 = Finding(pass_name="security", severity="critical", file="a.py", line=1, title="X", description="d", suggestion="s")
    f2 = Finding(pass_name="style", severity="nit", file="b.py", line=2, title="Y", description="d", suggestion="s")
    result = ReviewResult(findings=[f1, f2])

    engine.provider.complete = MagicMock(
        return_value=ProviderResponse(
            content='[{"id": "' + f1.id + '", "action": "keep", "reason": "ok"}, '
            '{"id": "' + f2.id + '", "action": "keep", "reason": "ok"}]'
        )
    )
    new_result = engine._run_verification(result, "diff", "ctx", 600)
    assert len(new_result.findings) == 2


def test_run_verification_drops_false_positives():
    from superseded.config import Config

    engine = ReviewEngine(provider=MagicMock(), config=Config(verify=True))
    f1 = Finding(pass_name="security", severity="critical", file="a.py", line=1, title="Real", description="d", suggestion="s")
    f2 = Finding(pass_name="style", severity="nit", file="b.py", line=2, title="Fake", description="d", suggestion="s")
    result = ReviewResult(findings=[f1, f2])

    engine.provider.complete = MagicMock(
        return_value=ProviderResponse(
            content='[{"id": "' + f1.id + '", "action": "keep", "reason": "ok"}, '
            '{"id": "' + f2.id + '", "action": "drop", "reason": "false positive"}]'
        )
    )
    new_result = engine._run_verification(result, "diff", "ctx", 600)
    assert len(new_result.findings) == 1
    assert new_result.findings[0].id == f1.id
    assert f2.verification == "dropped"


def test_run_verification_reestimates_severity():
    from superseded.config import Config

    engine = ReviewEngine(provider=MagicMock(), config=Config(verify=True))
    f = Finding(pass_name="performance", severity="important", file="a.py", line=5, title="Slow", description="d", suggestion="s")
    result = ReviewResult(findings=[f])
    engine.provider.complete = MagicMock(
        return_value=ProviderResponse(
            content='[{"id": "' + f.id + '", "action": "keep", "severity": "suggestion", "confidence": "low", "reason": "less severe"}]'
        )
    )
    new_result = engine._run_verification(result, "diff", "ctx", 600)
    assert new_result.findings[0].severity == "suggestion"
    assert new_result.findings[0].confidence == "low"
    assert new_result.findings[0].verified_severity == "suggestion"


def test_run_verification_failure_returns_original():
    from superseded.config import Config

    engine = ReviewEngine(provider=MagicMock(), config=Config(verify=True))
    f = Finding(pass_name="security", severity="critical", file="a.py", line=1, title="X", description="d", suggestion="s")
    result = ReviewResult(findings=[f])
    engine.provider.complete = MagicMock(side_effect=RuntimeError("timeout"))
    new_result = engine._run_verification(result, "diff", "ctx", 600)
    assert new_result is result
    assert len(new_result.warnings) == 1


def test_run_verification_missing_ids_kept():
    from superseded.config import Config

    engine = ReviewEngine(provider=MagicMock(), config=Config(verify=True))
    f1 = Finding(pass_name="security", severity="critical", file="a.py", line=1, title="Mentioned", description="d", suggestion="s")
    f2 = Finding(pass_name="style", severity="nit", file="b.py", line=2, title="Omitted", description="d", suggestion="s")
    result = ReviewResult(findings=[f1, f2])
    engine.provider.complete = MagicMock(
        return_value=ProviderResponse(content='[{"id": "' + f1.id + '", "action": "keep", "reason": "ok"}]')
    )
    new_result = engine._run_verification(result, "diff", "ctx", 600)
    assert len(new_result.findings) == 2


def test_run_verification_skips_when_no_findings():
    from superseded.config import Config

    engine = ReviewEngine(provider=MagicMock(), config=Config(verify=True))
    result = ReviewResult(findings=[])
    engine.provider.complete = MagicMock()
    new_result = engine._run_verification(result, "diff", "ctx", 600)
    assert engine.provider.complete.call_count == 0
    assert new_result is result
```

Note: `ReviewUsage` import is needed in the file — add `from superseded.models import Finding, ReviewResult, ReviewUsage`.

- [ ] **Step 8: Port `tests/test_cli.py`, `tests/test_server_worker.py`, `tests/test_server_config.py`, `tests/test_integration.py`**

For each file:
- Read the file end-to-end.
- Replace any `agent=` keyword argument to `Config(...)` with `provider=` (e.g. `Config(agent="opencode")` → `Config(provider="deepseek")`).
- Replace any `ReviewEngine.select("claude-code", ...)` or `"opencode"` / `"codex"` calls with `ReviewEngine.select("deepseek", ...)`.
- Replace any `--agent` CLI option in CliRunner invocations with `--provider`.
- Replace any `monkeypatch.setattr("superseded.review.executor.subprocess.run", ...)` (and similar `SubprocessExecutor` mocks) with `monkeypatch.setattr("superseded.review.engine.ReviewEngine.run_pass", fake_run_pass)` or by injecting a `FakeProvider` via `engine.provider = FakeProvider(...)`.
- In `tests/test_integration.py`, delete tests that exercised the subprocess agent path entirely; keep tests that exercise the diff/memory/output flow.
- In `tests/test_server_worker.py`, replace `SandboxSettings` construction with a `FakeProvider` injection; drop the `sandbox=` parameter to worker construction.
- In `tests/test_server_config.py`, remove tests for the deleted `sandbox_*` / `smolvm_*` fields; add a test that `ServerConfig.from_env()` reads `SUPERSEDED_DEEPSEEK_API_KEY`.

Run after each file: `uv run pytest tests/test_cli.py -v` (substitute the filename). Expected: PASS.

- [ ] **Step 9: Run the full test suite**

Run: `uv run pytest tests/ -q`
Expected: full suite passes. If failures remain, they are likely test references to deleted symbols (`Agent`, `AGENT_MAP`, `SubprocessExecutor`, `skill`, `detect_agents`) — fix each by either deleting the test (if it exercised a removed code path) or updating the assertion (if it was reusing a removed name as a fixture).

- [ ] **Step 10: Lint and format**

Run: `uv run ruff check src/ tests/ && uv run ruff format src/ tests/`
Expected: clean.

- [ ] **Step 11: Commit**

```bash
git add src/ tests/
git commit -m "refactor(engine): swap Agent/Session for Provider; wire CLI/server/config"
```

---

### Task 7: Delete dead modules and their tests

By this point nothing references the modules being deleted — the previous task removed all imports.

**Files:**
- Delete: `src/superseded/agents/__init__.py`, `base.py`, `claude_code.py`, `codex.py`, `opencode.py`, `parsing.py` (entire directory).
- Delete: `src/superseded/review/executor.py`.
- Delete: `src/superseded/skill.py`.
- Delete: `src/superseded/detection.py`.
- Delete: `tests/test_agents.py`, `tests/test_executor.py`, `tests/test_skill.py`, `tests/test_detection.py`.
- Modify: `pyproject.toml` (remove `optional-dependencies.sandbox`).

- [ ] **Step 1: Verify nothing imports the soon-to-be-deleted modules**

Run:
```bash
uv run python -c "
import subprocess
out = subprocess.check_output(['git', 'grep', '-l', '-E', 'superseded\\.(agents|skill|detection)|superseded\\.review\\.executor'], text=True)
print(out)
"
```
Expected: only the deletion candidates themselves (and their test files). If anything else shows up, fix it before continuing — Task 6 missed a reference.

- [ ] **Step 2: Delete the modules and their tests**

Run:
```bash
rm -r src/superseded/agents
rm src/superseded/review/executor.py
rm src/superseded/skill.py
rm src/superseded/detection.py
rm tests/test_agents.py tests/test_executor.py tests/test_skill.py tests/test_detection.py
```

- [ ] **Step 3: Remove `optional-dependencies.sandbox` from `pyproject.toml`**

Edit `pyproject.toml`: delete the entire `[project.optional-dependencies]` `sandbox = ["smolmachines"]` entry. If `graph` is the only remaining optional dep, keep the section header. The result:

```toml
[project.optional-dependencies]
graph = ["code-review-graph"]
```

- [ ] **Step 4: Re-sync (smolmachines was an extra)**

Run: `uv sync`
Expected: `smolmachines` removed from `.venv/`. `uv.lock` updated.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest tests/ -q`
Expected: full suite passes (now smaller — fewer test files).

- [ ] **Step 6: Lint and format**

Run: `uv run ruff check src/ tests/ && uv run ruff format src/ tests/`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: remove dead agents/executor/skill/detection modules"
```

---

### Task 8: Backward-compat for legacy `.superseded.yaml` and `SUPERSEDED_AGENT`

`SUPERSEDED_AGENT` aliasing was added inline in Task 6 step 6(c). This task adds the YAML-side hard-error and the `sandbox:` warn-and-ignore behavior, with TDD coverage.

**Files:**
- Modify: `src/superseded/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
import pytest


def test_load_config_hard_errors_on_legacy_cli_agent(tmp_path):
    """A YAML with `agent: opencode|claude-code|codex` is a hard error post-v0.6."""
    from superseded.config import load_config

    cfg_path = tmp_path / ".superseded.yaml"
    cfg_path.write_text("agent: opencode\n")
    with pytest.raises(ValueError, match="CLI agents were removed in v0.6.0"):
        load_config(cfg_path)


def test_load_config_hard_errors_on_legacy_claude_code(tmp_path):
    from superseded.config import load_config

    cfg_path = tmp_path / ".superseded.yaml"
    cfg_path.write_text("agent: claude-code\n")
    with pytest.raises(ValueError, match="CLI agents were removed"):
        load_config(cfg_path)


def test_load_config_hard_errors_on_legacy_codex(tmp_path):
    from superseded.config import load_config

    cfg_path = tmp_path / ".superseded.yaml"
    cfg_path.write_text("agent: codex\n")
    with pytest.raises(ValueError, match="CLI agents were removed"):
        load_config(cfg_path)


def test_load_config_legacy_agent_with_unknown_value_treats_as_provider(tmp_path):
    """If `agent:` has a value that isn't a known CLI agent, treat it as `provider:` and warn."""
    import warnings

    from superseded.config import load_config

    cfg_path = tmp_path / ".superseded.yaml"
    cfg_path.write_text("agent: openai\n")  # not a known CLI agent
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = load_config(cfg_path)
    assert cfg.provider == "openai"
    assert any("agent:" in str(w.message) for w in caught)


def test_load_config_ignores_legacy_sandbox_key(tmp_path):
    """A YAML with `sandbox: true` is silently ignored (with a warning)."""
    import warnings

    from superseded.config import load_config

    cfg_path = tmp_path / ".superseded.yaml"
    cfg_path.write_text("provider: deepseek\nsandbox: true\n")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = load_config(cfg_path)
    assert cfg.provider == "deepseek"
    assert not hasattr(cfg, "sandbox")
    assert any("sandbox:" in str(w.message) for w in caught)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v -k "legacy or ignores"`
Expected: FAIL — `load_config` does not yet validate or warn.

- [ ] **Step 3: Write minimal implementation**

Edit `src/superseded/config.py`'s `load_config` function:

```python
import warnings

_LEGACY_CLI_AGENTS = {"claude-code", "opencode", "codex"}


def load_config(path: Path | None = None) -> Config:
    if path is None:
        path = Path(".superseded.yaml")
    if not path.exists():
        return Config()
    data = yaml.safe_load(path.read_text()) or {}

    # Backward-compat: hard-error on legacy `agent: <cli-agent>` values.
    if "agent" in data:
        legacy_value = data["agent"]
        if isinstance(legacy_value, str) and legacy_value in _LEGACY_CLI_AGENTS:
            raise ValueError(
                "CLI agents were removed in v0.6.0. Set 'provider: deepseek' and "
                "$SUPERSEDED_DEEPSEEK_API_KEY. See MIGRATION.md."
            )
        # Unknown value — treat as `provider:` and warn.
        warnings.warn(
            "`agent:` in .superseded.yaml is renamed to `provider:`.",
            DeprecationWarning,
            stacklevel=2,
        )
        data.setdefault("provider", data.pop("agent"))

    # Backward-compat: silently drop `sandbox:` (no longer used).
    if "sandbox" in data:
        warnings.warn(
            "`sandbox:` in .superseded.yaml is no longer used (direct-API path has no subprocess to isolate).",
            DeprecationWarning,
            stacklevel=2,
        )
        data.pop("sandbox")

    return Config(**data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS — all config tests including the 5 new backward-compat ones.

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check src/superseded/config.py tests/test_config.py && uv run ruff format src/superseded/config.py tests/test_config.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/superseded/config.py tests/test_config.py
git commit -m "feat(config): hard-error on legacy CLI agent YAML; warn on sandbox key"
```

---

### Task 9: Verify `init` simplification (test coverage)

Task 6 step 6(g) rewrote `_run_init`. This task adds/updates tests so the new behavior is locked in.

**Files:**
- Modify: `tests/test_init.py`

- [ ] **Step 1: Read current `tests/test_init.py`**

Run: `cat tests/test_init.py` (or use the Read tool) to understand the existing test patterns. Existing tests likely mock `detect_agents`, `pick_agent`, `default_model_for` — those mocks need to be removed since those functions are deleted.

- [ ] **Step 2: Rewrite `tests/test_init.py`**

Replace the file with:

```python
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from superseded.cli import cli


def test_init_writes_minimal_yaml(tmp_path, monkeypatch):
    target = tmp_path / ".superseded.yaml"
    monkeypatch.delenv("SUPERSEDED_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/gh" if cmd == "gh" else None)
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 0
    text = target.read_text()
    assert "provider: deepseek" in text


def test_init_refuses_overwrite_without_force(tmp_path):
    target = tmp_path / ".superseded.yaml"
    target.write_text("provider: deepseek\n")
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 2


def test_init_force_overwrites(tmp_path, monkeypatch):
    target = tmp_path / ".superseded.yaml"
    target.write_text("provider: deepseek\n")
    monkeypatch.delenv("SUPERSEDED_DEEPSEEK_API_KEY", raising=False)
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--force", "--config", str(target)])
    assert result.exit_code == 0


def test_init_reports_missing_deepseek_key(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERSEDED_DEEPSEEK_API_KEY", raising=False)
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 0  # not an error, just a status line
    assert "SUPERSEDED_DEEPSEEK_API_KEY: not set" in result.output


def test_init_reports_present_deepseek_key(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERSEDED_DEEPSEEK_API_KEY", "sk-test")
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert result.exit_code == 0
    assert "SUPERSEDED_DEEPSEEK_API_KEY: set" in result.output


def test_init_reports_gh_presence(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/gh" if cmd == "gh" else None)
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert "gh CLI: found" in result.output


def test_init_reports_gh_absence(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    target = tmp_path / ".superseded.yaml"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(target)])
    assert "gh CLI: not found" in result.output
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_init.py -v`
Expected: PASS — all 7 init tests.

- [ ] **Step 4: Lint and format**

Run: `uv run ruff check tests/test_init.py && uv run ruff format tests/test_init.py`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add tests/test_init.py
git commit -m "test(init): cover simplified init (gh + DeepSeek key probes)"
```

---

### Task 10: Live integration tests (gated)

**Files:**
- Modify: `pyproject.toml` (markers + addopts)
- Create: `tests/test_live_deepseek.py`

- [ ] **Step 1: Register the `live` marker and exclude it by default**

Edit `pyproject.toml`'s `[tool.pytest.ini_options]`:

```toml
markers = [
    "postgres: requires a live Postgres (set SUPERSEDED_POSTGRES_TEST_DSN)",
    "live: makes real API calls (set SUPERSEDED_DEEPSEEK_API_KEY)",
]
addopts = "-m 'not postgres and not live'"
```

- [ ] **Step 2: Write the live tests**

Create `tests/test_live_deepseek.py`:

```python
"""Live round-trip tests against the real DeepSeek API.

Skipped by default (addopts excludes `live`). To run:

    SUPERSEDED_DEEPSEEK_API_KEY=sk-... uv run pytest -m live tests/test_live_deepseek.py -v
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _require_key():
    if not os.environ.get("SUPERSEDED_DEEPSEEK_API_KEY"):
        pytest.skip("SUPERSEDED_DEEPSEEK_API_KEY not set")


def test_live_deepseek_complete_returns_content():
    from superseded.providers import DeepSeekProvider

    provider = DeepSeekProvider()
    resp = provider.complete("Reply with the single word: pong", timeout=30.0)
    assert resp.content
    assert resp.prompt_tokens > 0
    assert resp.completion_tokens > 0


def test_live_engine_review_on_small_diff():
    from superseded.config import Config
    from superseded.providers import DeepSeekProvider
    from superseded.review.engine import ReviewEngine

    diff = (
        "diff --git a/example.py b/example.py\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        "+++ b/example.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+import os\n"
        "+password = os.environ['PASSWORD']\n"
        "+print(password)\n"
    )
    provider = DeepSeekProvider()
    engine = ReviewEngine(provider=provider, config=Config(verify=False))
    result = engine.review(diff=diff, passes=["security"], timeout=60)
    # Don't over-assert on the model's output, but it should flag the secret exposure.
    assert isinstance(result.findings, list)
    assert result.usage.prompt_tokens > 0
```

- [ ] **Step 3: Verify the suite excludes live tests by default**

Run: `uv run pytest tests/ -q`
Expected: `test_live_deepseek.py` is not collected (or shows as skipped); full suite still passes.

- [ ] **Step 4: Verify the marker is registered**

Run: `uv run pytest --markers | grep live`
Expected: a line like `@pytest.mark.live: makes real API calls ...`.

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check tests/test_live_deepseek.py pyproject.toml && uv run ruff format tests/test_live_deepseek.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml tests/test_live_deepseek.py
git commit -m "test: add live DeepSeek API round-trip tests (gated on env + marker)"
```

---

### Task 11: Migration docs

**Files:**
- Create: `MIGRATION.md`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `action.yml`

- [ ] **Step 1: Write `MIGRATION.md`**

Create `MIGRATION.md` at the repo root:

```markdown
# Migrating to superseded v0.6.0

v0.6.0 replaces the external-AI-CLI harness (claude-code, codex, opencode) with
a direct DeepSeek API call. The review engine, prompts, dedup, verification,
context gathering, memory store, and output formats are unchanged — only the
model-calling layer and the sandbox machinery around it changed.

## Required: set the DeepSeek API key

```bash
export SUPERSEDED_DEEPSEEK_API_KEY=sk-...
```

Get a key at <https://platform.deepseek.com>. The same env var is read by
`superseded review`, `superseded serve`, and the GitHub Action.

## `.superseded.yaml`

Rename `agent:` → `provider:`:

```yaml
# Before
agent: opencode
model: null

# After
provider: deepseek
model: deepseek-v4-flash   # or null to use the provider default
```

If your existing YAML has `agent: opencode`, `agent: claude-code`, or
`agent: codex`, superseded will refuse to start with a hard error. Set
`provider: deepseek` and configure the env var above.

The `sandbox:` key (if present) is silently ignored — direct-API calls have
no subprocess to isolate.

## Removed CLI flags

- `--sandbox` / `--no-sandbox`
- `--agent` (replaced by `--provider`)

## Removed CLI commands

- `superseded skill install`
- `superseded skill print`

(The skill command existed only to install a `SKILL.md` into other AI CLIs'
config dirs. With those CLIs no longer used, the command has no purpose.)

## Removed / renamed environment variables

| Old | New |
|---|---|
| `SUPERSEDED_AGENT` | `SUPERSEDED_PROVIDER` (the old name still works but emits a `DeprecationWarning`) |
| `SUPERSEDED_SANDBOX`, `SUPERSEDED_SANDBOX_KIND`, `SUPERSEDED_SANDBOX_TIMEOUT`, `SUPERSEDED_SANDBOX_KEEP_ON_ERROR`, `SUPERSEDED_SANDBOX_IO_MODE` | (removed — no sandbox) |
| `SUPERSEDED_SMOLVM_BINARY`, `SUPERSEDED_SMOLVM_IMAGE`, `SUPERSEDED_SMOLVM_IMAGE_CLAUDE`, `SUPERSEDED_SMOLVM_IMAGE_OPENCODE`, `SUPERSEDED_SMOLVM_IMAGE_CODEX` | (removed) |
| `SUPERSEDED_ALLOW_NO_SANDBOX` | (removed — no sandbox) |
| (none) | `SUPERSEDED_DEEPSEEK_API_KEY` (new, required) |

## Server operators

The server no longer needs KVM, `docker-sbx`, OCI images, or `smolmachines`.
It now runs anywhere Python 3.14+ runs. Required env at startup:

- `SUPERSEDED_DEEPSEEK_API_KEY` (refuses to start if missing)
- Existing GitHub App / port / database-url config unchanged.

The server's `ServerConfig` no longer has `sandbox_*` or `smolvm_*` fields.
The only new field is `deepseek_api_key`.

## Defaults

- Provider: `deepseek`
- Model: `deepseek-v4-flash` (override with `--model` / `SUPERSEDED_MODEL` / `.superseded.yaml`)
```

- [ ] **Step 2: Update `README.md`**

Find the setup/install section. Replace any instructions like "install claude-code, codex, or opencode" with:

```markdown
## Setup

1. Install superseded: `uv sync`
2. Get a DeepSeek API key at <https://platform.deepseek.com>.
3. Set it: `export SUPERSEDED_DEEPSEEK_API_KEY=sk-...`
4. (Optional) Run `uv run superseded init` to write a `.superseded.yaml`.
5. Review a PR: `uv run superseded review --pr 123`

See `MIGRATION.md` if you're upgrading from v0.5.x.
```

Remove any reference to `--sandbox`, `--agent`, `superseded skill`, or installing external AI CLIs.

- [ ] **Step 3: Update `AGENTS.md`**

Read the current `AGENTS.md` end-to-end, then make these edits:

- In the **Commands** block: remove the `superseded skill install` and `superseded skill print` lines. Remove the `--sandbox` mention.
- In **Architecture notes**:
  - Replace the paragraph starting "Agents are pluggable: subclass `agents/base.py:Agent`" with:
    > Providers are pluggable: subclass `providers/base.py:Provider` (implement `name`, `complete`), register in `PROVIDER_MAP` in `providers/__init__.py`. `complete()` returns a `ProviderResponse(content, prompt_tokens, completion_tokens, model, raw)`. The engine parses `content` via `providers/parsing.parse_findings_json`, which returns a list of dicts usable as `Finding(**item)`.
  - Replace the `superseded init` paragraph with:
    > `superseded init` is a non-interactive setup command: it probes PATH for `gh`, checks for `SUPERSEDED_DEEPSEEK_API_KEY`, checks for an installed `code-review-graph` at `.code-review-graph/`, and writes a `.superseded.yaml` via `config.write_config`. Refuses to overwrite without `--force`.
  - Delete the entire `superseded skill install` / `superseded skill print` paragraph.
  - Delete the paragraph about `SandboxExecutor` / `SmolvmExecutor` / `sbx` / `smolvm` / `SUPERSEDED_SANDBOX_KIND` entirely (the spec deletes all of it).
- In **Configuration precedence**: replace `agent` with `provider` and `SUPERSEDED_AGENT` with `SUPERSEDED_PROVIDER` in both the env-var and config-file mentions.
- **Preserve** the `**CRITICAL — superseded ≠ superpowers:**` block and the `**`except A, B:` is intentional, do not "fix" it.**` block verbatim.
- In **Packaging / GitHub Action**: update the env-var mention from `SUPERSEDED_SERVER_KEY` to also reference `SUPERSEDED_DEEPSEEK_API_KEY` as a required server-side env. Remove any `Dockerfile` references to gh / claude-code / opencode / codex installation in the `cli` target (the `cli` target now only needs `pip install .`).

- [ ] **Step 4: Update `action.yml`**

Read the current `action.yml`. It is a composite Action that POSTs to the review server. Add a note in the `inputs:` section (or a comment) documenting that the server must have `SUPERSEDED_DEEPSEEK_API_KEY` set. If there's an `env:` block referencing `SUPERSEDED_SERVER_KEY`, leave it; do not add the DeepSeek key as an Action input (it belongs on the server).

- [ ] **Step 5: Run a quick consistency check**

Run: `uv run pytest tests/ -q`
Expected: still passes (docs changes don't affect tests).

Run: `uv run ruff check src/ tests/`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add MIGRATION.md README.md AGENTS.md action.yml
git commit -m "docs: migration guide + README/AGENTS/action.yml updates for v0.6"
```

---

### Task 12: Version bump `0.5.0` → `0.6.0`

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Bump the version**

Edit `pyproject.toml`: change `version = "0.5.0"` to `version = "0.6.0"`.

- [ ] **Step 2: Re-sync**

Run: `uv sync`
Expected: `uv.lock` updated with the new version of `superseded`.

- [ ] **Step 3: Verify the version is visible**

Run: `uv run superseded --version`
Expected: `superseded, version 0.6.0`.

- [ ] **Step 4: Run the full suite one last time**

Run: `uv run pytest tests/ -q && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: bump version to 0.6.0"
```

---

## Verification

After all 12 tasks land:

- [ ] `uv run pytest tests/ -q` — full suite passes, default excludes `live` and `postgres`.
- [ ] `uv run pytest -m live tests/test_live_deepseek.py -v` — passes when `SUPERSEDED_DEEPSEEK_API_KEY` is set; otherwise skipped.
- [ ] `uv run ruff check src/ tests/` — clean.
- [ ] `uv run ruff format --check src/ tests/` — clean.
- [ ] `uv run superseded --version` — prints `0.6.0`.
- [ ] `uv run superseded review --diff HEAD~1..HEAD --format json` — runs end-to-end against real DeepSeek (requires the env var).
- [ ] `git log --oneline` — 12 commits, one per task (plus the spec commit from brainstorming).
- [ ] `grep -r "claude-code\|opencode\|codex\|SubprocessExecutor\|SandboxExecutor\|SmolvmExecutor\|AGENT_MAP\|skill install" src/ tests/` — returns no matches (except inside `MIGRATION.md` and `AGENTS.md` migration notes).
