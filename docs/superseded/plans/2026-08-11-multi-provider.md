# Multi-Provider Support (OpenAI + Anthropic) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OpenAI (GPT-5.6 via Responses API) and Anthropic (Claude via the `anthropic` SDK) as first-class providers behind the existing `Provider` protocol, normalize reasoning effort to `{low, medium, high, max}`, and update all docs + the stale `index.html` landing page.

**Architecture:** A new `OpenAICompatProvider` base in `providers/openai_compat.py` captures the ~90% shared DeepSeek/OpenAI code path (OpenAI-compatible client, key resolution, effort mapping, response→`ProviderResponse`); `DeepSeekProvider` becomes a thin subclass (constants only), `OpenAIProvider` a subclass with Responses API overrides (`_call`/`_parse_response`), and `AnthropicProvider` gets its own transport via the `anthropic` SDK while reusing `EFFORT_MAP`. `PROVIDER_MAP` grows to three entries; `ServerConfig`/serve gain provider-aware key checks; docs and `index.html` are rewritten to the multi-provider story.

**Tech Stack:** Python 3.14+, `openai` SDK (existing, reused for Responses API), `anthropic` SDK (new dep), click, pydantic v2, pytest. Run everything via `uv run`.

**Spec:** `docs/superseded/specs/2026-08-11-multi-provider-design.md`

---

## File Structure

**Create:**
- `src/superseded/providers/openai_compat.py` — `EFFORT_MAP` + `OpenAICompatProvider` base.
- `src/superseded/providers/openai.py` — `OpenAIProvider` (Responses API overrides).
- `src/superseded/providers/anthropic.py` — `AnthropicProvider`.
- `tests/test_live_openai.py` — gated live round-trip (`@pytest.mark.live`).
- `tests/test_live_anthropic.py` — gated live round-trip (`@pytest.mark.live`).

**Modify:**
- `pyproject.toml` — add `anthropic>=0.40.0` dep; bump version `0.6.0` → `0.7.0` (Task 9).
- `src/superseded/providers/deepseek.py` — refactor to `DeepSeekProvider(OpenAICompatProvider)` constants-only subclass.
- `src/superseded/providers/__init__.py` — `PROVIDER_MAP` gains `openai` + `anthropic`; `__all__` update.
- `src/superseded/config.py` — `reasoning_effort` Literal widens to `["low", "medium", "high", "max"]`.
- `src/superseded/cli.py` — `--provider` help text, `--reasoning-effort` Choice widen, `init` key reporting, serve provider factory + provider-aware key check.
- `src/superseded/server/config.py` — `openai_api_key` + `anthropic_api_key` fields + env reads.
- `src/superseded/server/worker.py` — no functional change (verify only; the effort Literal widening touches the type annotation).
- `tests/test_providers.py` — new OpenAI/Anthropic/EFFORT_MAP tests; DeepSeek tests preserved against the refactored base.
- `tests/test_config.py`, `tests/test_cli.py`, `tests/test_server_config.py` — widening + new providers coverage.
- `README.md`, `docs/superseded/index.md`, `docs/superseded/configuration.md`, `docs/superseded/review.md`, `docs/superseded/server.md`, `MIGRATION.md`, `AGENTS.md` — multi-provider docs.
- `index.html` — rewrite stale sections (sandbox/microVM, agent cards, skill card) to the provider story.

**Net change:** roughly +700 / −100 across src, tests, and docs.

---

### Task 1: Add `anthropic` SDK dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the dep**

Edit `pyproject.toml`'s `[project] dependencies` list: add `"anthropic>=0.40.0",` in alphabetical position (between `"alembic>=1.13.0",` and `"asyncpg>=0.30.0",`).

- [ ] **Step 2: Sync**

Run: `uv sync`
Expected: `uv.lock` updated; `anthropic` + transitive deps (`anyio`, `distro`, `httpx`, `jiter`, `pydantic`, `sniffio`, `typing-extensions`) installed.

- [ ] **Step 3: Verify import**

Run: `uv run python -c "from anthropic import Anthropic; print(Anthropic)"
Expected: `<class 'anthropic.Anthropic'>`.

- [ ] **Step 4: Verify baseline suite**

Run: `uv run pytest tests/ -q`
Expected: **557 passed, 2 failed (pre-existing test_migrations.py), 1 skipped, 2 deselected** — unchanged.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): add anthropic SDK for Claude provider"
```

---

### Task 2: Create `OpenAICompatProvider` base + refactor DeepSeek onto it

This is the highest-risk refactor: the existing `DeepSeekProvider` must keep identical behavior (9 unit tests in `tests/test_providers.py` are the guard).

**Files:**
- Create: `src/superseded/providers/openai_compat.py`
- Modify: `src/superseded/providers/deepseek.py`

- [ ] **Step 1: Write the failing tests first (EFFORT_MAP + base behavior)**

Append to `tests/test_providers.py`:

```python
from superseded.providers.openai_compat import EFFORT_MAP, OpenAICompatProvider


def test_effort_map_deepseek():
    assert EFFORT_MAP["deepseek"] == {"low": "low", "medium": "high", "high": "high", "max": "max"}


def test_effort_map_openai():
    assert EFFORT_MAP["openai"] == {"low": "low", "medium": "medium", "high": "high", "max": "max"}


def test_effort_map_anthropic():
    assert EFFORT_MAP["anthropic"] == {"low": "low", "medium": "medium", "high": "high", "max": "xhigh"}


def test_openai_compat_provider_requires_configured_subclass():
    """The base must refuse instantiation without subclass class-attributes."""
    with pytest.raises((TypeError, ProviderConfigError)):
        OpenAICompatProvider()
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_providers.py -v -k "effort_map or openai_compat"`
Expected: FAIL — `ModuleNotFoundError: No module named 'superseded.providers.openai_compat'`.

- [ ] **Step 3: Create `providers/openai_compat.py`**

```python
from __future__ import annotations

import os
from typing import Literal

from openai import OpenAI

from superseded.providers.base import ProviderConfigError, ProviderResponse

EFFORT_MAP: dict[str, dict[str, str]] = {
    "deepseek": {"low": "low", "medium": "high", "high": "high", "max": "max"},
    "openai": {"low": "low", "medium": "medium", "high": "high", "max": "max"},
    "anthropic": {"low": "low", "medium": "medium", "high": "high", "max": "xhigh"},
}


class OpenAICompatProvider:
    """Base for OpenAI-compatible providers (DeepSeek, OpenAI).

    Subclasses set: ``name``, ``api_key_env``, ``base_url``, ``default_model``,
    ``effort_key`` (one of EFFORT_MAP's keys). ``OpenAIProvider`` additionally
    overrides ``_call`` and ``_parse_response`` to use the Responses API.
    """

    name: str
    api_key_env: str
    base_url: str
    default_model: str
    effort_key: str

    def __init__(self, api_key: str | None = None) -> None:
        resolved = api_key or os.environ.get(self.api_key_env)
        if not resolved:
            raise ProviderConfigError(
                f"No {self.name} API key. Set ${self.api_key_env} or pass api_key=."
            )
        self._client = OpenAI(api_key=resolved, base_url=self.base_url, max_retries=2)
        self._default_model = self.default_model

    def _map_effort(self, effort: str) -> str | None:
        return EFFORT_MAP[self.effort_key].get(effort)

    def _call(self, kwargs: dict):
        return self._client.chat.completions.create(**kwargs)

    def _parse_response(self, resp, resolved: str) -> ProviderResponse:
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

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        timeout: float = 600.0,
        temperature: float = 0.0,
        reasoning_effort: Literal["low", "medium", "high", "max"] | None = None,
    ) -> ProviderResponse:
        resolved = model or self._default_model
        kwargs: dict = {
            "model": resolved,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "timeout": timeout,
        }
        if reasoning_effort is not None:
            mapped = self._map_effort(reasoning_effort)
            if mapped is not None:
                kwargs["reasoning_effort"] = mapped
        resp = self._call(kwargs)
        return self._parse_response(resp, resolved)
```

- [ ] **Step 4: Refactor `providers/deepseek.py`**

Replace the whole file:

```python
from __future__ import annotations

from superseded.providers.openai_compat import OpenAICompatProvider

DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"
DEEPSEEK_API_KEY_ENV = "SUPERSEDED_DEEPSEEK_API_KEY"


class DeepSeekProvider(OpenAICompatProvider):
    """Direct DeepSeek Chat Completions client (OpenAI-compatible)."""

    name = "deepseek"
    api_key_env = DEEPSEEK_API_KEY_ENV
    base_url = DEEPSEEK_DEFAULT_BASE_URL
    default_model = DEEPSEEK_DEFAULT_MODEL
    effort_key = "deepseek"
```

Note: the existing DeepSeek unit tests construct `DeepSeekProvider(api_key="sk-test")` and monkeypatch `superseded.providers.deepseek.OpenAI` — the `from openai import OpenAI` now lives in `openai_compat.py`, so those monkeypatch targets MUST be updated (Task 3 handles the test updates; the tests will fail until then).

- [ ] **Step 5: Run the new tests**

Run: `uv run pytest tests/test_providers.py -v -k "effort_map or openai_compat"`
Expected: PASS — 4 new tests. (Existing DeepSeek tests will FAIL at this point because their monkeypatch target moved — that's expected; fixed in Task 3.)

- [ ] **Step 6: Lint**

Run: `uv run ruff check src/superseded/providers/ tests/test_providers.py && uv run ruff format src/superseded/providers/ tests/test_providers.py`
Expected: clean.

- [ ] **Step 7: Commit (known-broken state OK — Task 3 fixes immediately)**

```bash
git add src/superseded/providers/openai_compat.py src/superseded/providers/deepseek.py tests/test_providers.py
git commit -m "feat(providers): add OpenAICompatProvider base; refactor DeepSeek onto it"
```

---

### Task 3: Update DeepSeek test monkeypatch targets

The DeepSeek tests patch `superseded.providers.deepseek.OpenAI`, but the `OpenAI` import moved to `openai_compat.py`. Update every patch target.

**Files:**
- Modify: `tests/test_providers.py`

- [ ] **Step 1: Update monkeypatch targets**

In `tests/test_providers.py`, every `monkeypatch.setattr("superseded.providers.deepseek.OpenAI", ...)` becomes `monkeypatch.setattr("superseded.providers.openai_compat.OpenAI", ...)`. Grep to find all:

Run: `grep -n "superseded.providers.deepseek.OpenAI" tests/test_providers.py`
Expected: N matches (the deepseek tests use it as the FakeClient patch point).

Replace each with `"superseded.providers.openai_compat.OpenAI"`.

- [ ] **Step 2: Run the full provider test file**

Run: `uv run pytest tests/test_providers.py -v`
Expected: PASS — all tests (base 5 + parsing 9 + deepseek 9 + effort_map/openai_compat 4 = 27).

- [ ] **Step 3: Verify the whole suite**

Run: `uv run pytest tests/ -q`
Expected: **561 passed, 2 failed (pre-existing test_migrations.py), 1 skipped, 2 deselected** (557 + 4 new effort/compat tests).

- [ ] **Step 4: Commit**

```bash
git add tests/test_providers.py
git commit -m "test(providers): retarget DeepSeek OpenAI monkeypatches to openai_compat"
```

---

### Task 4: Create `OpenAIProvider` (Responses API)

**Files:**
- Create: `src/superseded/providers/openai.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_providers.py`:

```python
from superseded.providers.openai import (
    OPENAI_API_KEY_ENV,
    OPENAI_DEFAULT_BASE_URL,
    OPENAI_DEFAULT_MODEL,
    OpenAIProvider,
)


def _fake_responses(*, text="[]", input_tokens=11, output_tokens=6, model="gpt-5.6-terra"):
    """Quacks like an openai Responses API response."""
    usage = type(
        "Usage", (), {"input_tokens": input_tokens, "output_tokens": output_tokens}
    )()
    return type("Resp", (), {"output_text": text, "usage": usage, "model": model})()


def test_openai_constants():
    assert OPENAI_API_KEY_ENV == "SUPERSEDED_OPENAI_API_KEY"
    assert OPENAI_DEFAULT_BASE_URL == "https://api.openai.com/v1"
    assert OPENAI_DEFAULT_MODEL == "gpt-5.6-terra"


def test_openai_provider_name():
    p = OpenAIProvider(api_key="sk-test")
    assert p.name == "openai"


def test_openai_provider_raises_when_no_key(monkeypatch):
    monkeypatch.delenv(OPENAI_API_KEY_ENV, raising=False)
    with pytest.raises(ProviderConfigError, match="No openai API key"):
        OpenAIProvider()


def test_openai_complete_uses_responses_api(monkeypatch):
    """OpenAIProvider must call responses.create (not chat.completions)."""
    captured = {}

    class FakeClient:
        def __init__(self, **kw):
            captured["init_kwargs"] = kw

        @property
        def responses(self):
            return self

        def create(self, **kw):
            captured["create_kwargs"] = kw
            return _fake_responses(text='[{"severity": "critical"}]', input_tokens=42, output_tokens=7)

    monkeypatch.setattr("superseded.providers.openai_compat.OpenAI", FakeClient)
    p = OpenAIProvider(api_key="sk-test")
    resp = p.complete("the prompt", reasoning_effort="max")
    assert resp.content == '[{"severity": "critical"}]'
    assert resp.prompt_tokens == 42
    assert resp.completion_tokens == 7
    # Responses API: single user message translated to `input` (a string).
    assert captured["create_kwargs"]["input"] == "the prompt"
    assert "messages" not in captured["create_kwargs"]
    assert captured["create_kwargs"]["reasoning_effort"] == "max"


def test_openai_complete_maps_effort(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kw):
            pass

        @property
        def responses(self):
            return self

        def create(self, **kw):
            captured.update(kw)
            return _fake_responses()

    monkeypatch.setattr("superseded.providers.openai_compat.OpenAI", FakeClient)
    p = OpenAIProvider(api_key="sk-test")
    p.complete("p", reasoning_effort="medium")
    assert captured["reasoning_effort"] == "medium"
    p.complete("p", reasoning_effort="max")
    assert captured["reasoning_effort"] == "max"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_providers.py -v -k "openai"`
Expected: FAIL — `ModuleNotFoundError: No module named 'superseded.providers.openai'`.

- [ ] **Step 3: Create `providers/openai.py`**

```python
from __future__ import annotations

from superseded.providers.base import ProviderResponse
from superseded.providers.openai_compat import OpenAICompatProvider

OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
OPENAI_DEFAULT_MODEL = "gpt-5.6-terra"
OPENAI_API_KEY_ENV = "SUPERSEDED_OPENAI_API_KEY"


class OpenAIProvider(OpenAICompatProvider):
    """GPT-5.6 via OpenAI's Responses API."""

    name = "openai"
    api_key_env = OPENAI_API_KEY_ENV
    base_url = OPENAI_DEFAULT_BASE_URL
    default_model = OPENAI_DEFAULT_MODEL
    effort_key = "openai"

    def _call(self, kwargs: dict):
        # Responses API uses `input` (not Chat Completions' `messages`) and
        # accepts a plain string. We always send exactly one user message.
        input_ = kwargs.pop("messages")[0]["content"]
        return self._client.responses.create(input=input_, **kwargs)

    def _parse_response(self, resp, resolved: str) -> ProviderResponse:
        content = getattr(resp, "output_text", None) or ""
        usage = getattr(resp, "usage", None)
        return ProviderResponse(
            content=content,
            prompt_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
            completion_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
            model=getattr(resp, "model", resolved),
            raw=resp,
        )
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_providers.py -v -k "openai"`
Expected: PASS — all 5 OpenAI tests.

- [ ] **Step 5: Full suite**

Run: `uv run pytest tests/ -q`
Expected: **566 passed, 2 failed, 1 skipped, 2 deselected** (561 + 5 new).

- [ ] **Step 6: Lint**

Run: `uv run ruff check src/superseded/providers/ tests/test_providers.py && uv run ruff format src/superseded/providers/ tests/test_providers.py`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/superseded/providers/openai.py tests/test_providers.py
git commit -m "feat(providers): add OpenAIProvider via Responses API"
```

---

### Task 5: Create `AnthropicProvider`

**Files:**
- Create: `src/superseded/providers/anthropic.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_providers.py`:

```python
from superseded.providers.anthropic import (
    ANTHROPIC_API_KEY_ENV,
    ANTHROPIC_DEFAULT_MODEL,
    ANTHROPIC_MAX_TOKENS,
    AnthropicProvider,
)


def _fake_messages(*, blocks=("[]",), input_tokens=13, output_tokens=8, model="claude-sonnet-5"):
    """Quacks like an anthropic Messages API response."""
    content = []
    for i, b in enumerate(blocks):
        content.append(type("Block", (), {"type": "text", "text": b})())
    usage = type("Usage", (), {"input_tokens": input_tokens, "output_tokens": output_tokens})()
    return type("Resp", (), {"content": content, "usage": usage, "model": model})()


def test_anthropic_constants():
    assert ANTHROPIC_API_KEY_ENV == "SUPERSEDED_ANTHROPIC_API_KEY"
    assert ANTHROPIC_DEFAULT_MODEL == "claude-sonnet-5"
    assert ANTHROPIC_MAX_TOKENS == 128_000


def test_anthropic_provider_name():
    p = AnthropicProvider(api_key="sk-test")
    assert p.name == "anthropic"


def test_anthropic_provider_raises_when_no_key(monkeypatch):
    monkeypatch.delenv(ANTHROPIC_API_KEY_ENV, raising=False)
    with pytest.raises(ProviderConfigError, match="No anthropic API key"):
        AnthropicProvider()


def test_anthropic_complete_uses_messages_api(monkeypatch):
    captured = {}

    class FakeMessages:
        def __init__(self, **kw):
            captured["init_kwargs"] = kw

        @property
        def messages(self):
            return self

        def create(self, **kw):
            captured["create_kwargs"] = kw
            return _fake_messages(blocks=("first ", "second"), input_tokens=5, output_tokens=3)

    monkeypatch.setattr("superseded.providers.anthropic.Anthropic", FakeMessages)
    p = AnthropicProvider(api_key="sk-test")
    resp = p.complete("the prompt", reasoning_effort="max")
    # text blocks joined
    assert resp.content == "first second"
    assert resp.prompt_tokens == 5
    assert resp.completion_tokens == 3
    assert captured["create_kwargs"]["messages"] == [{"role": "user", "content": "the prompt"}]
    assert captured["create_kwargs"]["max_tokens"] == 128_000
    # effort mapped: max -> xhigh (Anthropic vocabulary)
    assert captured["create_kwargs"]["effort"] == "xhigh"


def test_anthropic_complete_maps_effort(monkeypatch):
    captured = {}

    class FakeMessages:
        def __init__(self, **kw):
            pass

        @property
        def messages(self):
            return self

        def create(self, **kw):
            captured.update(kw)
            return _fake_messages()

    monkeypatch.setattr("superseded.providers.anthropic.Anthropic", FakeMessages)
    p = AnthropicProvider(api_key="sk-test")
    p.complete("p", reasoning_effort="low")
    assert captured["effort"] == "low"
    p.complete("p", reasoning_effort="high")
    assert captured["effort"] == "high"
    p.complete("p", reasoning_effort="max")
    assert captured["effort"] == "xhigh"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_providers.py -v -k "anthropic"`
Expected: FAIL — `ModuleNotFoundError: No module named 'superseded.providers.anthropic'`.

- [ ] **Step 3: Create `providers/anthropic.py`**

```python
from __future__ import annotations

import os
from typing import Literal

from anthropic import Anthropic

from superseded.providers.base import ProviderConfigError, ProviderResponse
from superseded.providers.openai_compat import EFFORT_MAP

ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-5"
ANTHROPIC_API_KEY_ENV = "SUPERSEDED_ANTHROPIC_API_KEY"
ANTHROPIC_MAX_TOKENS = 128_000


class AnthropicProvider:
    """Claude via the Anthropic Messages API (adaptive thinking)."""

    name = "anthropic"

    def __init__(self, api_key: str | None = None) -> None:
        resolved = api_key or os.environ.get(ANTHROPIC_API_KEY_ENV)
        if not resolved:
            raise ProviderConfigError(
                f"No Anthropic API key. Set ${ANTHROPIC_API_KEY_ENV} or pass api_key=."
            )
        self._client = Anthropic(api_key=resolved, max_retries=2)
        self._default_model = ANTHROPIC_DEFAULT_MODEL

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        timeout: float = 600.0,
        temperature: float = 0.0,
        reasoning_effort: Literal["low", "medium", "high", "max"] | None = None,
    ) -> ProviderResponse:
        resolved = model or self._default_model
        kwargs: dict = {
            "model": resolved,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": ANTHROPIC_MAX_TOKENS,
            "temperature": temperature,
            "timeout": timeout,
        }
        if reasoning_effort is not None:
            mapped = EFFORT_MAP["anthropic"].get(reasoning_effort)
            if mapped is not None:
                kwargs["effort"] = mapped
        resp = self._client.messages.create(**kwargs)
        # Anthropic returns content as a list of blocks; join text blocks.
        content = "".join(
            b.text for b in resp.content if getattr(b, "type", "") == "text"
        )
        return ProviderResponse(
            content=content,
            prompt_tokens=resp.usage.input_tokens,
            completion_tokens=resp.usage.output_tokens,
            model=resp.model,
            raw=resp,
        )
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_providers.py -v -k "anthropic"`
Expected: PASS — all 5 Anthropic tests.

- [ ] **Step 5: Full suite**

Run: `uv run pytest tests/ -q`
Expected: **571 passed, 2 failed, 1 skipped, 2 deselected** (566 + 5 new).

- [ ] **Step 6: Lint**

Run: `uv run ruff check src/superseded/providers/ tests/test_providers.py && uv run ruff format src/superseded/providers/ tests/test_providers.py`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/superseded/providers/anthropic.py tests/test_providers.py
git commit -m "feat(providers): add AnthropicProvider via Messages API"
```

---

### Task 6: Register providers in `PROVIDER_MAP` + widen effort Literal

**Files:**
- Modify: `src/superseded/providers/__init__.py`
- Modify: `src/superseded/config.py`
- Modify: `src/superseded/review/engine.py` (type annotation only)
- Modify: `src/superseded/cli.py` (Choice widen + help text + init key reporting + serve factory)
- Modify: `src/superseded/server/config.py`
- Modify: `src/superseded/server/worker.py` (type annotation only)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_providers.py`:

```python
def test_provider_map_has_three_providers():
    from superseded.providers import PROVIDER_MAP

    assert set(PROVIDER_MAP) == {"deepseek", "openai", "anthropic"}
    assert PROVIDER_MAP["openai"] is OpenAIProvider
    assert PROVIDER_MAP["anthropic"] is AnthropicProvider
```

Append to `tests/test_config.py`:

```python
def test_config_reasoning_effort_accepts_medium():
    from superseded.config import Config

    assert Config(reasoning_effort="medium").reasoning_effort == "medium"
    assert Config().reasoning_effort == "max"
```

Append to `tests/test_cli.py` (find the `resolve_reasoning_effort` test from the earlier commit and extend):

```python
def test_cli_provider_choices_include_all_providers():
    from click.testing import CliRunner

    from superseded.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--help"])
    assert result.exit_code == 0
    assert "deepseek, openai, anthropic" in result.output
    assert "medium" in result.output  # widened effort choice
```

Append to `tests/test_server_config.py`:

```python
def test_server_config_reads_openai_and_anthropic_keys(monkeypatch):
    from superseded.server.config import ServerConfig

    monkeypatch.setenv("SUPERSEDED_OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("SUPERSEDED_ANTHROPIC_API_KEY", "sk-anthropic")
    cfg = ServerConfig.from_env()
    assert cfg.openai_api_key == "sk-openai"
    assert cfg.anthropic_api_key == "sk-anthropic"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_providers.py tests/test_config.py tests/test_cli.py tests/test_server_config.py -v -k "three_providers or medium or choices or openai_and_anthropic"`
Expected: FAIL — `PROVIDER_MAP` has only deepseek; Config Literal rejects `medium`; serve key check unchanged.

- [ ] **Step 3: Update `providers/__init__.py`**

```python
from __future__ import annotations

from superseded.providers.anthropic import AnthropicProvider
from superseded.providers.base import Provider, ProviderConfigError, ProviderResponse
from superseded.providers.deepseek import DeepSeekProvider
from superseded.providers.openai import OpenAIProvider

PROVIDER_MAP: dict[str, type[Provider]] = {
    "deepseek": DeepSeekProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
}

__all__ = [
    "PROVIDER_MAP",
    "AnthropicProvider",
    "DeepSeekProvider",
    "OpenAIProvider",
    "Provider",
    "ProviderConfigError",
    "ProviderResponse",
]
```

- [ ] **Step 4: Widen the effort Literal in `config.py`**

Change `reasoning_effort: Literal["low", "high", "max"] = "max"` to `reasoning_effort: Literal["low", "medium", "high", "max"] = "max"`.

- [ ] **Step 5: Widen the type annotations in `review/engine.py` and `server/worker.py`**

Both files annotate `self.reasoning_effort` / the config attr with `Literal["low", "high", "max"]` — widen to `Literal["low", "medium", "high", "max"]`. Grep first: `grep -n 'Literal\["low", "high", "max"\]' src/` and update all hits (should be engine.py + worker.py).

- [ ] **Step 6: Update `cli.py`**

(a) `--provider` help text → `"Model provider (deepseek, openai, anthropic)"`.

(b) `--reasoning-effort` Choice → `click.Choice(["low", "medium", "high", "max"])`.

(c) `_run_init` key reporting — replace the current DeepSeek-only check block with:

```python
    key_status = {
        "deepseek": bool(os.environ.get("SUPERSEDED_DEEPSEEK_API_KEY")),
        "openai": bool(os.environ.get("SUPERSEDED_OPENAI_API_KEY")),
        "anthropic": bool(os.environ.get("SUPERSEDED_ANTHROPIC_API_KEY")),
    }
    if any(key_status.values()):
        _status(
            "API keys: "
            + ", ".join(f"{k} {'✓' if v else '✗'}" for k, v in key_status.items())
        )
    else:
        _status("API keys: none set — set one of SUPERSEDED_DEEPSEEK_API_KEY, "
                "SUPERSEDED_OPENAI_API_KEY, SUPERSEDED_ANTHROPIC_API_KEY.")

    cfg = Config(provider="deepseek")
    write_config(cfg, target)
    _status(f"Wrote {target} (provider: deepseek)")
```

(Keep the existing overwrite/gh/CRG checks; read the current `_run_init` before editing.)

(e) Update `tests/test_init.py` — the two key-reporting tests assert the OLD format strings (`"SUPERSEDED_DEEPSEEK_API_KEY: set"` / `": not set"`), which the new `API keys: ...` reporting replaces. Update them:

- `test_init_reports_missing_deepseek_key` → rename to `test_init_reports_no_api_keys` and assert `"API keys: none set" in result.output`.
- `test_init_reports_present_deepseek_key` → rename to `test_init_reports_configured_keys` and assert `"API keys: deepseek ✓" in result.output` and `"openai ✗" in result.output` (monkeypatch only the DeepSeek key, matching the current test).

Read the current file first; keep the other 5 tests unchanged.

(d) `serve` — add a provider-aware key check and factory. In `serve` (after the `require_configured` try/except and replacing the current `if not config.deepseek_api_key:` block):

```python
    KEY_ENV_BY_PROVIDER = {
        "deepseek": "SUPERSEDED_DEEPSEEK_API_KEY",
        "openai": "SUPERSEDED_OPENAI_API_KEY",
        "anthropic": "SUPERSEDED_ANTHROPIC_API_KEY",
    }

    if config.provider not in KEY_ENV_BY_PROVIDER:
        click.echo(f"Error: unknown provider {config.provider!r}", err=True)
        sys.exit(2)
    if not getattr(config, f"{config.provider}_api_key", None):
        click.echo(
            f"Error: {KEY_ENV_BY_PROVIDER[config.provider]} must be set to serve.",
            err=True,
        )
        sys.exit(2)
```

And replace the `provider=DeepSeekProvider(api_key=config.deepseek_api_key)` argument in the `ReviewWorker(...)` construction with a factory call:

```python
def _build_server_provider(config: ServerConfig) -> Provider:
    if config.provider == "openai":
        return OpenAIProvider(api_key=config.openai_api_key)
    if config.provider == "anthropic":
        return AnthropicProvider(api_key=config.anthropic_api_key)
    return DeepSeekProvider(api_key=config.deepseek_api_key)
```

Place `_build_server_provider` near the top of cli.py (module-level, after the resolvers) and call it in `serve`. Add the imports: `from superseded.providers import DeepSeekProvider, OpenAIProvider, AnthropicProvider` (extend the existing `from superseded.providers import ...` line).

- [ ] **Step 7: Update `server/config.py`**

Add fields after `deepseek_api_key`:

```python
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
```

And in `from_env()`, after the deepseek block:

```python
        openai_api_key = os.environ.get("SUPERSEDED_OPENAI_API_KEY")
        if openai_api_key:
            kwargs["openai_api_key"] = openai_api_key
        anthropic_api_key = os.environ.get("SUPERSEDED_ANTHROPIC_API_KEY")
        if anthropic_api_key:
            kwargs["anthropic_api_key"] = anthropic_api_key
```

- [ ] **Step 8: Run the targeted tests**

Run: `uv run pytest tests/test_providers.py tests/test_config.py tests/test_cli.py tests/test_server_config.py tests/test_init.py -v`
Expected: all pass.

- [ ] **Step 9: Full suite**

Run: `uv run pytest tests/ -q`
Expected: **575 passed, 2 failed, 1 skipped, 2 deselected** (571 + 4 new).

- [ ] **Step 10: Lint**

Run: `uv run ruff check src/ tests/ && uv run ruff format src/ tests/`
Expected: clean.

- [ ] **Step 11: Commit**

```bash
git add src/ tests/
git commit -m "feat(providers): register openai+anthropic; widen effort to low/medium/high/max"
```

---

### Task 7: Live tests for OpenAI + Anthropic

**Files:**
- Create: `tests/test_live_openai.py`
- Create: `tests/test_live_anthropic.py`

- [ ] **Step 1: Create `tests/test_live_openai.py`**

```python
"""Live round-trip test against the real OpenAI API (GPT-5.6).

Skipped by default. To run:

    SUPERSEDED_OPENAI_API_KEY=sk-... uv run pytest -m live tests/test_live_openai.py -v
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _require_key():
    if not os.environ.get("SUPERSEDED_OPENAI_API_KEY"):
        pytest.skip("SUPERSEDED_OPENAI_API_KEY not set")


def test_live_openai_complete_returns_content():
    from superseded.providers import OpenAIProvider

    provider = OpenAIProvider()
    resp = provider.complete("Reply with the single word: pong", timeout=30.0)
    assert resp.content
    assert resp.prompt_tokens > 0
    assert resp.completion_tokens > 0
```

- [ ] **Step 2: Create `tests/test_live_anthropic.py`**

```python
"""Live round-trip test against the real Anthropic API (Claude).

Skipped by default. To run:

    SUPERSEDED_ANTHROPIC_API_KEY=sk-ant-... uv run pytest -m live tests/test_live_anthropic.py -v
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _require_key():
    if not os.environ.get("SUPERSEDED_ANTHROPIC_API_KEY"):
        pytest.skip("SUPERSEDED_ANTHROPIC_API_KEY not set")


def test_live_anthropic_complete_returns_content():
    from superseded.providers import AnthropicProvider

    provider = AnthropicProvider()
    resp = provider.complete("Reply with the single word: pong", timeout=30.0)
    assert resp.content
    assert resp.prompt_tokens > 0
    assert resp.completion_tokens > 0
```

- [ ] **Step 3: Verify default suite excludes them**

Run: `uv run pytest tests/ -q`
Expected: still **575 passed, 2 failed, 1 skipped, 2 deselected** — the new live files are deselected (the existing `-m 'not postgres and not live'` addopts already handles them).

- [ ] **Step 4: Lint**

Run: `uv run ruff check tests/test_live_openai.py tests/test_live_anthropic.py && uv run ruff format tests/test_live_openai.py tests/test_live_anthropic.py`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add tests/test_live_openai.py tests/test_live_anthropic.py
git commit -m "test: add gated live round-trip tests for openai and anthropic providers"
```

---

### Task 8: Docs + landing page

**Files:**
- Modify: `README.md`
- Modify: `docs/superseded/index.md`
- Modify: `docs/superseded/configuration.md`
- Modify: `docs/superseded/review.md`
- Modify: `docs/superseded/server.md`
- Modify: `MIGRATION.md`
- Modify: `AGENTS.md`
- Modify: `index.html`

- [ ] **Step 1: `README.md`**

Read it end-to-end first. Then:
- Setup section: after the DeepSeek bullet, add:

```markdown
**Prefer a different provider?** Set one of:

| Provider | Env var | Default model |
|---|---|---|
| DeepSeek | `SUPERSEDED_DEEPSEEK_API_KEY` | `deepseek-v4-flash` |
| OpenAI | `SUPERSEDED_OPENAI_API_KEY` | `gpt-5.6-terra` |
| Anthropic | `SUPERSEDED_ANTHROPIC_API_KEY` | `claude-sonnet-5` |

Then `superseded review --provider openai` (or `anthropic`). Get keys at
platform.deepseek.com, platform.openai.com, and console.anthropic.com.
```

- In Usage → CLI examples, add after the first review example:

```bash
# Review with a different provider
superseded review --pr 123 --provider openai
superseded review --pr 123 --provider anthropic
```

- Action section: the line about `SUPERSEDED_DEEPSEEK_API_KEY` becomes provider-dependent — note the server needs the key matching `provider:` (deepseek/openai/anthropic).

- [ ] **Step 2: `docs/superseded/index.md`**

- Line 3: "It calls the DeepSeek API directly" → "It calls your choice of DeepSeek, OpenAI, or Anthropic API directly".
- Line 11 ("Direct API provider" row): "Reviews run over the DeepSeek API" → "Reviews run over DeepSeek, OpenAI, or Anthropic — no CLI agents or sandboxes to manage".
- Quick Start: add after the DeepSeek key line:

```bash
# ...or use OpenAI or Anthropic instead
export SUPERSEDED_OPENAI_API_KEY=sk-...    # https://platform.openai.com
export SUPERSEDED_ANTHROPIC_API_KEY=sk-ant-...  # https://console.anthropic.com
```

- Requirements section: replace the DeepSeek bullet with:

```markdown
- **A provider API key** (one of):
  - `SUPERSEDED_DEEPSEEK_API_KEY` — platform.deepseek.com
  - `SUPERSEDED_OPENAI_API_KEY` — platform.openai.com
  - `SUPERSEDED_ANTHROPIC_API_KEY` — console.anthropic.com
```

- [ ] **Step 3: `docs/superseded/configuration.md`**

Read first. Then:
- Add a provider table (deepseek/openai/anthropic + env var + default model) near the top.
- Add the three key env vars to the env table (alongside `SUPERSEDED_PROVIDER`/`SUPERSEDED_MODEL`).
- Update the `reasoning_effort` row: values `low/medium/high/max`, default `max`, note "mapped per provider (anthropic max → xhigh)".

- [ ] **Step 4: `docs/superseded/review.md`**

Read first. Then in the "Provider Selection" section: add openai/anthropic alongside deepseek with their defaults; note `--provider` values; the model-defaults table gains `gpt-5.6-terra` / `claude-sonnet-5`.

- [ ] **Step 5: `docs/superseded/server.md`**

Read first. Then: env table gains `SUPERSEDED_OPENAI_API_KEY` / `SUPERSEDED_ANTHROPIC_API_KEY` rows (same "optional unless provider selected" wording as deepseek). Update the key-requirement paragraph: the server requires the key matching `provider:`.

- [ ] **Step 6: `MIGRATION.md`**

Add a short section at the end:

```markdown
## New providers (v0.7)

v0.7 adds OpenAI (GPT-5.6, Responses API) and Anthropic (Claude, Messages API)
providers alongside DeepSeek. Set `SUPERSEDED_OPENAI_API_KEY` or
`SUPERSEDED_ANTHROPIC_API_KEY` and pass `--provider openai` / `--provider
anthropic` (or set `provider:` in `.superseded.yaml`). Reasoning effort gained
a `medium` level: `low | medium | high | max` (default `max`; Anthropic maps
`max` to `xhigh`). No existing configs break.
```

- [ ] **Step 7: `AGENTS.md`**

Read first. Then:
- Runtime external deps note: add "and `anthropic`" to the provider SDK mention; key env vars list gains the two new ones.
- Architecture notes: `PROVIDER_MAP` sentence gains "openai: OpenAIProvider (Responses API), anthropic: AnthropicProvider (Messages API)".
- The "5 pass names" / engine paragraph: no change needed (provider-agnostic already).
- Preserve the `**CRITICAL — superseded ≠ superpowers:**` and `except A, B:` blocks verbatim.

- [ ] **Step 8: `index.html` (the landing page)**

This is the big one. Read it end-to-end first (904 lines). Then:

(a) Find the install/setup section (around lines 685-690, the `.install-cmd` block with `uv tool install`). After the install command, add API-key setup steps showing all three providers (mirror the README table).

(b) Delete the "AI agent" / "claude-code · opencode · codex" references — search for `claude-code`, `opencode`, `codex`, `microVM`, `sbx`, `smolvm`, `skill install`, `Pluggable agents`, `Agent skill`. The affected blocks:
- The sandbox/isolation section (~line 805: "Each agent executes inside an ephemeral microVM &mdash; ...") — DELETE the whole section.
- The feature card with `AI agent` + `claude-code · opencode · codex` (~line 814) — DELETE or replace.
- The `iso-point` cards ("Host untouched", ~line 830) — DELETE with the section.
- The "Pluggable agents" card (~line 844) — REPLACE with a "Provider choice" card: "DeepSeek, OpenAI, or Anthropic — one API key, one `--provider` flag. No CLI agents or sandboxes."
- The "Agent skill" card (~line 849, mentions `superseded skill install`) — DELETE.
- The CTA / feature-summary text that mentions agents/sandboxes — update to the provider story.

(c) Update any remaining copy that claims agents/sandboxes/CLI CLIs exist.

(d) Keep the overall design/HTML structure and styling untouched — only content changes.

- [ ] **Step 9: Verify docs consistency**

Run: `grep -rn "claude-code\|opencode\|codex\|smolvm\|skill install\|microVM" README.md docs/superseded/ index.html | grep -v specs/ | grep -v plans/`
Expected: no matches (except possibly in MIGRATION.md where v0.5→v0.6 history legitimately mentions them — check each remaining hit is historical, not present-tense).

Run: `uv run pytest tests/ -q`
Expected: unchanged (**575 passed, 2 failed, 1 skipped, 2 deselected**).

- [ ] **Step 10: Commit**

```bash
git add README.md docs/superseded/ MIGRATION.md AGENTS.md index.html
git commit -m "docs: multi-provider setup, configuration, server guide, and landing page"
```

---

### Task 9: Version bump `0.6.0` → `0.7.0`

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Bump**

Change `version = "0.6.0"` to `version = "0.7.0"` in `pyproject.toml`.

- [ ] **Step 2: Sync**

Run: `uv sync`
Expected: `uv.lock` updated.

- [ ] **Step 3: Verify**

Run: `uv run superseded --version`
Expected: `superseded, version 0.7.0`.

- [ ] **Step 4: Full suite + lint**

Run: `uv run pytest tests/ -q && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: **575 passed, 2 failed (pre-existing test_migrations.py), 1 skipped, 2 deselected**; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: bump version to 0.7.0"
```

---

## Verification

After all 9 tasks land:

- [ ] `uv run pytest tests/ -q` — **575 passed, 2 failed (pre-existing test_migrations.py), 1 skipped, 2 deselected**.
- [ ] `uv run ruff check src/ tests/` — clean.
- [ ] `uv run ruff format --check src/ tests/` — clean.
- [ ] `uv run superseded --version` — `0.7.0`.
- [ ] `uv run superseded review --provider openai --help` and `--provider anthropic --help` — help text shows all three providers + medium effort.
- [ ] `grep -rn "claude-code\|opencode\|codex\|smolvm\|skill install\|microVM" README.md docs/superseded/index.md docs/superseded/configuration.md docs/superseded/review.md docs/superseded/server.md index.html` — no matches.
- [ ] Live tests available: `SUPERSEDED_OPENAI_API_KEY=... uv run pytest -m live tests/test_live_openai.py` (and anthropic analog) — pass when keys are set.
- [ ] `git log --oneline` — 9 commits, one per task.
