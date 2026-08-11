# Multi-Provider Support (OpenAI + Anthropic) — Design

**Date:** 2026-08-11
**Status:** Draft

## Problem

Superseded currently supports exactly one model provider — DeepSeek — behind
the `Provider` protocol (`providers/`). The abstraction was designed for
"one class per provider", but only `DeepSeekProvider` ships. Users who prefer
OpenAI's GPT-5.6 family or Anthropic's Claude models (or want to compare
review quality across providers) must run a separate tool.

The `index.html` landing page is also badly stale after the v0.6 refactor: it
still advertises microVM sandboxes, claude-code/opencode/codex CLI agents, and
`superseded skill install` — all deleted. README and docs are DeepSeek-only.

## Goals / Non-goals

**Goals**

- Add `OpenAIProvider` (GPT-5.6 family via the Responses API) and
  `AnthropicProvider` (Claude via the official `anthropic` SDK) as first-class
  providers behind the existing `Provider` protocol.
- Normalize reasoning-effort to `{low, medium, high, max}` across providers,
  mapped per-provider to native values (DeepSeek `max`, OpenAI `max`,
  Anthropic `xhigh`). Default: `max` everywhere (matching the DeepSeek
  default chosen earlier).
- New API keys: `SUPERSEDED_OPENAI_API_KEY`, `SUPERSEDED_ANTHROPIC_API_KEY`
  (mirroring `SUPERSEDED_DEEPSEEK_API_KEY`).
- Refactor DeepSeek/OpenAI onto a shared `OpenAICompatProvider` base — they
  are ~90% identical (both OpenAI-compatible APIs); Anthropic gets its own
  transport but reuses the effort map.
- Server path: provider-aware key check + provider construction factory.
- Update docs: README, docs/superseded/*, AGENTS.md, MIGRATION.md, and the
  stale `index.html` landing page.

**Non-goals**

- Streaming / partial output (unchanged from current design).
- Local/on-prem providers (still one class per provider when they come).
- Per-provider reasoning-effort knobs (one normalized knob only).
- OpenAI via Chat Completions — the GPT-5.6 docs route through the Responses
  API; that's what we implement.
- Model catalogs / dynamic model listing from the APIs. Defaults are
  constants; `--model` overrides as today.

## Design

### File structure

```
src/superseded/providers/
├── base.py          # Provider protocol, ProviderResponse, ProviderConfigError (unchanged)
├── openai_compat.py # NEW: OpenAICompatProvider base + EFFORT_MAP
├── deepseek.py      # DeepSeekProvider(OpenAICompatProvider) — constants only
├── openai.py        # NEW: OpenAIProvider(OpenAICompatProvider) — Responses API overrides
├── anthropic.py     # NEW: AnthropicProvider (own transport via anthropic SDK)
├── parsing.py       # unchanged
└── __init__.py      # PROVIDER_MAP gains "openai" + "anthropic"
```

### `OpenAICompatProvider` base (`providers/openai_compat.py`)

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

### Thin subclasses

`providers/deepseek.py` (~25 lines):

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

`providers/openai.py` (~50 lines) — same shape plus the Responses API overrides:

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

### `AnthropicProvider` (`providers/anthropic.py`, ~70 lines, own transport)

Anthropic is a different API shape, so no inheritance from the OpenAI-compat
base — but it reuses `EFFORT_MAP` and the same `complete()` contract:

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

Key choices:

- **`max_tokens=128_000`** (the model max for sonnet-5): Anthropic requires
  `max_tokens` and counts reasoning tokens toward it. No cap → no truncation
  risk; the 5-pass fan-out plus effort mapping is the cost guard. (Chosen by
  the user over 32768 and 16384.)
- **`effort` param** maps our normalized `max` → Anthropic's `xhigh`; sonnet-5
  uses adaptive thinking (always on), so no explicit `thinking` block needed.
- **`temperature=0.0`** kept for parity with the other providers (Claude
  supports it; thinking modes may ignore it — same as DeepSeek).

### `PROVIDER_MAP` (`providers/__init__.py`)

```python
PROVIDER_MAP: dict[str, type[Provider]] = {
    "deepseek": DeepSeekProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
}
```

### Keys / defaults summary

| Provider | Env var | Base URL | Default model |
|---|---|---|---|
| deepseek | `SUPERSEDED_DEEPSEEK_API_KEY` | `api.deepseek.com` | `deepseek-v4-flash` |
| openai | `SUPERSEDED_OPENAI_API_KEY` | `api.openai.com/v1` | `gpt-5.6-terra` |
| anthropic | `SUPERSEDED_ANTHROPIC_API_KEY` | (anthropic SDK default) | `claude-sonnet-5` |

## Config / CLI / server wiring

### `Config` (`config.py`)

`reasoning_effort: Literal["low", "medium", "high", "max"] = "max"` — the
Literal widens from `["low", "high", "max"]` (backward-compatible: existing
values remain valid; `medium` is additive).

### CLI (`cli.py`)

- `--provider` help text: `"Model provider (deepseek, openai, anthropic)"`.
- `--reasoning-effort` Choice widens to `["low", "medium", "high", "max"]`.
- Model defaults come from each provider class (`gpt-5.6-terra`,
  `claude-sonnet-5`); no CLI change needed for `--model`.

### Server (`server/config.py`, `cli.py` serve, `server/worker.py`)

- `ServerConfig` gains `openai_api_key` and `anthropic_api_key` (env-loaded
  from `SUPERSEDED_OPENAI_API_KEY` / `SUPERSEDED_ANTHROPIC_API_KEY`).
  `deepseek_api_key` stays.
- The serve-time key check becomes provider-aware:

```python
KEY_ENV_BY_PROVIDER = {
    "deepseek": "SUPERSEDED_DEEPSEEK_API_KEY",
    "openai": "SUPERSEDED_OPENAI_API_KEY",
    "anthropic": "SUPERSEDED_ANTHROPIC_API_KEY",
}
```

`serve` requires the key for `config.provider` (error names the missing env
var). Other providers' keys may be absent.

- Provider construction becomes a factory used by `serve`:

```python
def _build_server_provider(config: ServerConfig) -> Provider:
    if config.provider == "openai":
        return OpenAIProvider(api_key=config.openai_api_key)
    if config.provider == "anthropic":
        return AnthropicProvider(api_key=config.anthropic_api_key)
    return DeepSeekProvider(api_key=config.deepseek_api_key)
```

- `server/worker.py`: unchanged (already takes a `Provider` + sets
  `engine.reasoning_effort` from config).

### `superseded init`

Reports all three keys (e.g. `API keys: deepseek ✓, openai ✗, anthropic ✗`).
Written config still defaults `provider: deepseek`.

## Backward-compat

- Existing `provider: deepseek` configs keep working unchanged.
- `reasoning_effort: max` (and low/high) remain valid after the Literal
  widening.
- No required new keys — `provider: openai` just needs its env var.

## Docs / landing page

- **`index.html`** (root landing, 904 lines): remove the stale sandbox/microVM
  section, the "AI agent · claude-code · opencode · codex" card, the "Pluggable
  agents" card, and the "Agent skill / `superseded skill install`" card.
  Replace with a "Provider choice" story (DeepSeek / OpenAI / Anthropic, one
  env var each). Update install + setup steps to show any of the three keys.
- **`README.md`**: setup section shows all three key env vars + `--provider`
  examples; Action section notes the server key requirement is
  provider-dependent.
- **`docs/superseded/index.md`**: "calls the DeepSeek API directly" →
  "calls your choice of DeepSeek, OpenAI, or Anthropic API directly";
  Requirements gains the three keys ("any one").
- **`docs/superseded/configuration.md`**: provider table, key env vars,
  widened `reasoning_effort` values + per-provider mapping note.
- **`docs/superseded/review.md`**: Provider Selection section gains
  openai/anthropic; model defaults table.
- **`docs/superseded/server.md`**: server env table gains the two new keys;
  note that only the configured provider's key is required.
- **`MIGRATION.md`**: brief "New providers (v0.7)" section.
- **`AGENTS.md`**: `PROVIDER_MAP` three entries; runtime deps note gains
  `anthropic`; key env var table.

## Dependencies

`anthropic` SDK added to `pyproject.toml` (`anthropic>=0.40.0`). `openai` SDK
already present (reused for the Responses API). No other changes.

## Testing

**Unit (no network):**
- `tests/test_providers.py`:
  - `OpenAIProvider`: fake `OpenAI` with `responses.create` returning a
    synthetic Responses object (`output_text`, `usage.input_tokens/output_tokens`).
    Verify content + usage extraction, `reasoning_effort` forwarded (max→max),
    missing-key `ProviderConfigError`.
  - `AnthropicProvider`: fake `anthropic.Anthropic` with `messages.create`
    returning a Messages object (list of text blocks, `usage`). Verify
    text-block joining, `effort` mapping (max→xhigh), `max_tokens=128000`,
    missing-key `ProviderConfigError`.
  - `EFFORT_MAP` unit tests for all three providers (deepseek medium→high,
    anthropic max→xhigh, etc.).
  - Existing DeepSeek tests pass unchanged against the refactored base (the
    fake-client shape is preserved).
- `tests/test_config.py`: `reasoning_effort` accepts `medium`.
- `tests/test_cli.py`: `--provider openai` / `--provider anthropic` select the
  right class; widened effort Choice.
- `tests/test_server_config.py`: `openai_api_key`/`anthropic_api_key` env
  reads; provider-aware serve key check.

**Live (gated `@pytest.mark.live`, env-gated per key):**
- `tests/test_live_openai.py` (gated on `SUPERSEDED_OPENAI_API_KEY`) and
  `tests/test_live_anthropic.py` (gated on `SUPERSEDED_ANTHROPIC_API_KEY`).
  One round-trip test each (`complete` → non-empty content + token counts),
  mirroring `test_live_deepseek.py`.

## Versioning

Additive feature: `0.6.0` → `0.7.0`.

## Out of scope / future work

- More providers (local vLLM, Groq, Mistral, …) — one class + one
  `PROVIDER_MAP` line each.
- Dynamic model catalog listing (query the APIs for available models).
- Per-provider cost guards / budgets (token usage is already surfaced).
- Streaming.
