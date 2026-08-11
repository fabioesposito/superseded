# Direct-API Provider (DeepSeek) — Design

**Date:** 2026-08-11
**Status:** Draft

## Problem

Superseded's reliability is bounded by its dependencies on three external AI
CLIs — `claude-code`, `codex`, `opencode`. Each is invoked as a subprocess via
`SubprocessExecutor` (or `SandboxExecutor`/`SmolvmExecutor` on the server), and
each is a black box from superseded's perspective: opaque failure modes,
uncontrollable prompt assembly, model selection mediated by the CLI's own
config, version drift between CLI releases, and per-CLI output conventions that
force a separate `Agent` subclass and parser for each. On the server path the
problem is worse — defending against malicious PRs requires booting microVMs
(`docker-sbx` or `smolvm`), which adds a KVM/Hypervisor.framework requirement,
OCI image management, per-agent credential seeding, and ~966 lines of executor
machinery.

DeepSeek offers an OpenAI-compatible Chat Completions API. Calling it directly
removes the CLI layer entirely: one HTTP request per pass, typed errors,
built-in retries, deterministic output, and no subprocess or sandbox to manage.
Future OpenAI-compatible providers (OpenAI itself, Anthropic via compatibility,
local vLLM) become a one-class addition rather than a new agent integration.

This spec covers the harness swap only — replacing the CLI-subprocess layer
with a direct DeepSeek API call and ripping out the now-dead CLI/sandbox
machinery. CodeRabbit-parity product features (walkthrough summaries, web
dashboard, multi-forge connectors, IDE extensions, multi-step agent loops) are
deferred to future specs that build on this foundation.

## Goals / Non-goals

**Goals**

- A single reliable model-calling path: `DeepSeekProvider.complete(prompt)`
  via the `openai` Python SDK pointed at DeepSeek's `base_url`.
- A clean `Provider` abstraction (`providers/base.py`) so adding future
  direct-API providers is one class + one line in `PROVIDER_MAP`.
- Delete the now-dead subprocess/sandbox/skill/detection machinery. Net change
  roughly −1500 / +200 lines.
- Keep everything that already works: 5-pass fan-out, prompts, dedup merger,
  verifier, context gathering (conventions/specs/usage/static), memory store,
  output formats, code-review-graph integration.
- Server becomes deployable on any plain host — no KVM, no Docker, no OCI
  images.
- Backward-incompatible change is signalled by a minor version bump
  (`0.5.0` → `0.6.0`) and documented in `MIGRATION.md`.

**Non-goals**

- Multi-provider in this spec. Only `DeepSeekProvider` ships.
- CodeRabbit-parity product features (summaries, dashboard, multi-forge, IDE).
- Streaming / partial-output UX. The `progress(pass_name, "start"|"done")`
  callback still fires per pass; intra-pass streaming is deferred.
- Local / on-prem model hosting.
- A deprecation cycle. Pre-1.0 SemVer + the small surface area of user-visible
  breakage (one env var to set, one YAML key to rename) make a clean break
  cheaper than keeping the unreliable code path alive for one release.

## Design

### Approach considered

Three shapes were considered:

- **A. Replace `Agent` with a `Provider` protocol; delete the executor
  machinery.** The current `Agent` abstraction (`agents/base.py:7`) is shaped
  around subprocess — `build_command()` returns a shell command,
  `parse_output()` parses stdout, `is_available()` does `shutil.which`. Once
  no CLI exists to shell out to, that shape is wrong. **(Chosen.)**
- **B. Keep `Agent` shape, stub out `build_command`, swap executor
  underneath.** Smaller diff, but `Agent.build_command()` becomes permanently
  meaningless cargo and the 966-line executor file mostly survives despite
  half of it (sandboxes) being unreachable.
- **C. Side-by-side, don't unify.** Keep CLI path untouched, add a parallel
  direct-API branch. Smallest diff today, two code paths forever. Violates
  the "abstraction left clean" goal.

HTTP client choice inside A:

- **(i) `openai` SDK pointed at DeepSeek's `base_url`.** One small dep.
  Gives retries, typed errors, and every future OpenAI-compatible provider
  becomes a one-line `base_url` change. **(Chosen.)**
- **(ii) Raw `httpx` to `/v1/chat/completions`.** Zero new deps but ~150
  extra lines of retry/auth/streaming/error code reinvented.

### Provider abstraction (`providers/base.py`)

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ProviderResponse:
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    raw: object | None = None


class Provider(Protocol):
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

`model` defaults to `None` so the engine can call `provider.complete(prompt)`
and let the provider use its configured default; an explicit `model=`
overrides per-call.

Returning a struct (not just `str`) lets the engine surface token usage into
`ReviewResult.usage`, record which model the provider actually resolved
(aliases like `deepseek-v4-flash` may map to a versioned ID), and keep the raw
SDK object for debugging.

### `DeepSeekProvider` (`providers/deepseek.py`)

```python
from __future__ import annotations

import os

from openai import OpenAI

from superseded.providers.base import ProviderResponse

DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"
DEEPSEEK_API_KEY_ENV = "SUPERSEDED_DEEPSEEK_API_KEY"


class ProviderConfigError(RuntimeError):
    """A provider is misconfigured (e.g. missing API key)."""


class DeepSeekProvider:
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
        content = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        return ProviderResponse(
            content=content,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            model=getattr(resp, "model", resolved),
            raw=resp,
        )
```

Key choices:

- **`max_retries=2` on the client** for 429/5xx. With 5 concurrent passes
  this stays well under DeepSeek's per-key rate ceiling.
- **`temperature=0.0` default** for determinism (same for verify pass).
- **`reasoning_content` ignored.** Same code path handles `deepseek-v4-flash`
  today and any chain-of-thought model swapped in later via `--model`.
- **`ProviderConfigError` at construction**, so missing-key failures fire
  before any passes start, not 30s into a review.
- **No `.superseded.yaml` field for the key.** Secrets in repo files are a
  footgun. Env var or explicit `api_key=` arg only.
- **`base_url`, `default_model` configurable** so the server/operator can
  point at a proxy or pin a model without code changes.

### Parsing helper (`providers/parsing.py`)

Replaces `agents/parsing.py`. ~30 lines. Strips ```` ```json ... ``` ```` fences
if present, `json.loads`, validates the result is a `list`. Top-level dict (a
single finding) is rejected — the prompt specifies an array, so a dict is
schema drift the existing retry path handles.

The retry-on-schema-drift logic in `engine.py:64-77` stays unchanged — it
already operates on the parsed list of dicts, calling the provider a second
time with `build_retry_prompt`.

### `ReviewEngine` refactor (`review/engine.py`)

| Before | After |
|---|---|
| `__init__(self, agent: Agent, config)` | `__init__(self, provider: Provider, config)` |
| `select(agent_name, model, config)` looks up `AGENT_MAP` | `select(provider_name, model, config)` looks up `PROVIDER_MAP` |
| `_run_and_validate` calls `sess.run(cmd, prompt, timeout)` then `agent.parse_output(stdout)` | `_run_and_validate` calls `provider.complete(prompt, model=..., timeout=...)` then `_parse_findings_json(resp.content, pass_name)` |
| `run_pass(..., sess: Session)` | `run_pass(...)` — no session, calls `provider.complete` directly |
| `review(..., cwd, env, executor)` | `review(...)` — drops `cwd`, `env`, `executor` (no subprocess) |
| `available()` check raises if CLI missing | Construction-time `ProviderConfigError` if key missing |
| `_run_verification(..., sess)` | `_run_verification(...)` — no session |

`ThreadPoolExecutor(max_workers=max(1, len(passes)))` for the 5 concurrent
passes stays exactly as-is — it now wraps 5 concurrent HTTP calls instead of 5
subprocesses. The retry-once-on-validation-error and verify-pass behaviors
(`review/engine.py:52-80`, `98-169`) are unchanged.

### Token usage tracking

`ReviewResult` grows a `usage: ReviewUsage` field:

```python
class ReviewUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    per_pass: dict[str, tuple[int, int]] = {}  # pass -> (prompt, completion)
```

`run_pass` returns `(findings, ProviderResponse)` internally; `review()`
accumulates `ProviderResponse.prompt_tokens`/`completion_tokens` into
`result.usage`. The existing `audit/stats.py` machinery can surface
cost-per-review without changing shape.

### `PROVIDER_MAP` (`providers/__init__.py`)

```python
PROVIDER_MAP: dict[str, type[Provider]] = {
    "deepseek": DeepSeekProvider,
}
```

Future OpenAI/Anthropic/local providers are one class each + one line in
`PROVIDER_MAP`. An OpenAI provider would be ~30 lines (same shape, different
`base_url` and env var).

## What gets deleted

**Entire modules:**

- `src/superseded/agents/` — entire directory deleted (`base.py`,
  `claude_code.py`, `codex.py`, `opencode.py`, `parsing.py`, and the empty
  `__init__.py`). `parsing.py` is replaced by `providers/parsing.py`.
- `src/superseded/review/executor.py` — all 966 lines: `Session`,
  `AgentExecutor`, `SubprocessExecutor`, `SandboxExecutor`, `SmolvmExecutor`,
  `make_sandbox_executor`, `agent_credential_files`, `_DEFAULT_PROVIDER_KEYS`,
  `_resolve_boot_source`, `_relocate_under_root`, all auth-seeding helpers.
- `src/superseded/skill.py` — canonical `SKILL.md` generator. No AI CLIs to
  install it into.
- `src/superseded/detection.py` — wraps `AGENT_MAP` + `Agent.is_available()`.

**CLI surface (`cli.py`):**

- `superseded skill install` / `superseded skill print` subcommands and
  `_run_skill_install` helper.
- `--sandbox` / `--no-sandbox` flag and `resolve_sandbox()`.
- `_select_executor`, `_resolve_smolvm_image`.
- `serve` cmd's sandbox wiring block (`cli.py:986-1041`): `SandboxSettings`
  construction, `SUPERSEDED_ALLOW_NO_SANDBOX` refusal logic.

**Config surface:**

- `Config.sandbox` field (`config.py`).
- `ServerConfig.sandbox_enabled`, `sandbox_kind`, `sandbox_timeout`,
  `sandbox_keep_on_error`, `sandbox_io_mode`, `smolvm_binary`, `smolvm_image`,
  `smolvm_image_claude`, `smolvm_image_opencode`, `smolvm_image_codex` and the
  env-var loaders for each (`server/config.py`).
- `SandboxSettings` dataclass and `_agent_smolvm_image` /
  `_sandbox_unavailable_msg` in `server/worker.py`.
- The sandbox branch in `server/worker.py` (`make_sandbox_executor` call at
  worker.py:498-523).

**Env vars (dead):** `SUPERSEDED_SANDBOX`, `SUPERSEDED_SANDBOX_KIND`,
`SUPERSEDED_SANDBOX_TIMEOUT`, `SUPERSEDED_SANDBOX_KEEP_ON_ERROR`,
`SUPERSEDED_SANDBOX_IO_MODE`, `SUPERSEDED_SMOLVM_BINARY`,
`SUPERSEDED_SMOLVM_IMAGE`, `SUPERSEDED_SMOLVM_IMAGE_CLAUDE`,
`SUPERSEDED_SMOLVM_IMAGE_OPENCODE`, `SUPERSEDED_SMOLVM_IMAGE_CODEX`,
`SUPERSEDED_ALLOW_NO_SANDBOX`.

**`pyproject.toml`:** `optional-dependencies.sandbox = ["smolmachines"]`
removed. New runtime dep added: `openai>=1.50.0`.

**Tests:** all tests targeting `executor.py`, `skill` install/print,
`detection.py`, sandbox/smolvm behavior, and any subprocess-mock tests in
`test_engine.py` / `test_integration.py`. Replaced per the testing section
below.

## CLI / config surface

| Before | After |
|---|---|
| `--agent` flag (default `opencode`) | `--provider` flag (default `deepseek`) |
| `SUPERSEDED_AGENT` env var | `SUPERSEDED_PROVIDER` env var |
| `Config.agent: str = "opencode"` | `Config.provider: str = "deepseek"` |
| `resolve_agent()` | `resolve_provider()` (`resolve_model()` unchanged) |
| `SUPERSEDED_DEEPSEEK_API_KEY` (did not exist) | Required env var; checked at provider construction |
| `.superseded.yaml` `agent:` key | `provider:` key |

**Backward-compat on env var:** if `SUPERSEDED_AGENT` is set and
`SUPERSEDED_PROVIDER` is not, read the old name and emit a
`DeprecationWarning`.

**`.superseded.yaml` backward-compat on load:**

- If `provider:` is present: use it.
- If only `agent:` is present and value is `opencode`/`claude-code`/`codex`:
  **hard error** with the migration message:
  `"CLI agents were removed in v0.6.0. Set 'provider: deepseek' and $SUPERSEDED_DEEPSEEK_API_KEY. See MIGRATION.md."`
- If only `agent:` is present with any other value: treat as `provider:`,
  warn.
- If `sandbox:` key is present: ignore + warn.

Precedence for `provider`/`model` is unchanged from today's `agent`/`model`:
**env vars > CLI flags > config file** (see `resolve_provider`/`resolve_model`
in `cli.py`).

## `superseded init` simplification

Currently probes PATH for `claude-code`/`codex`/`opencode` via
`detection.py` plus `gh`, picks a default agent + model, writes
`.superseded.yaml`. After:

- No AI CLI probing (deleted; `detection.py` removed).
- Probe for `gh` only (still needed for `gh pr diff`).
- Check `SUPERSEDED_DEEPSEEK_API_KEY` is set; print a setup hint if missing
  (do not fail — the user may be running `init` before creating the key).
- Write minimal `.superseded.yaml` with `provider: deepseek` and default
  model.

## Server path (`server/worker.py`, `server/config.py`)

- All `sandbox_*` and `smolvm_*` fields removed from `ServerConfig`.
- New field `deepseek_api_key: str | None = None`, populated from
  `SUPERSEDED_DEEPSEEK_API_KEY` in `from_env()`.
- Server refuses to start if the key is missing (replaces the old "refusing
  to serve without a sandbox" check at `cli.py:986-1003`).
- Worker constructs `DeepSeekProvider(api_key=server_config.deepseek_api_key)`
  **once** at startup and reuses it for every review.
- No per-review `cwd`/env setup — passes are stateless HTTP calls.
- `_agent_smolvm_image`, `_sandbox_unavailable_msg`, `SandboxSettings`, and
  the sandbox-branch (`worker.py:498-523`) all deleted.

**Operational consequence:** the server runs anywhere Python runs. No KVM, no
Docker, no OCI images, no `docker-sbx`. Deployable on fly.io/render/railway
free tiers, a $5 VPS, or AWS Lambda (with appropriate packaging).

## Testing

**Unit tests (no network):**

- `tests/test_providers.py`: monkeypatch the `OpenAI` client with a fake
  returning a synthetic `ChatCompletion`. Verify content extraction, token
  usage propagation, `reasoning_content` ignored, `ProviderConfigError` on
  missing key.
- `tests/test_parsing.py`: `_parse_findings_json` covers bare JSON,
  ```` ```json ````-fenced, leading/trailing prose, malformed JSON, top-level
  dict (rejected).
- `tests/test_engine.py`: port existing tests — replace the fake `Agent`
  with a fake `Provider` returning canned `ProviderResponse`. The
  retry-on-validation-error test stays.
- All merger/verifier/prompt/context tests unchanged.

**Live integration (gated):**

- `tests/test_live_deepseek.py` marked `@pytest.mark.live`, gated on
  `SUPERSEDED_DEEPSEEK_API_KEY` env presence. `pyproject.toml`
  `addopts = "-m 'not postgres and not live'"` so default `uv run pytest`
  stays free.
- 2 tests: real `DeepSeekProvider.complete` round-trip on a tiny prompt +
  real `ReviewEngine.review` on a ~10-line diff. Bounded cost (~$0.01/run).

**Deleted:** all tests targeting `executor.py`, `skill` install/print,
`detection.py`, sandbox/smolvm behavior, and subprocess-mock tests in
`test_engine.py` / `test_integration.py`.

## Migration

- **`MIGRATION.md`** at repo root documenting:
  - Required: set `SUPERSEDED_DEEPSEEK_API_KEY`.
  - `.superseded.yaml`: `agent:` → `provider:`. Remove `sandbox:`. Old agent
    values (`opencode`/`claude-code`/`codex`) are no longer valid.
  - Removed flags: `--sandbox`/`--no-sandbox`.
  - Removed commands: `superseded skill install`, `superseded skill print`.
  - Removed env vars: all `SUPERSEDED_SANDBOX_*`, `SUPERSEDED_SMOLVM_*`,
    `SUPERSEDED_AGENT` (replaced by `SUPERSEDED_PROVIDER`).
  - Removed server config: `sandbox_*`, `smolvm_*`. Added: `deepseek_api_key`.
  - Operational note: server no longer needs KVM or Docker.
- **`README.md`** setup section rewritten: "install claude-code/codex/opencode"
  → "set `SUPERSEDED_DEEPSEEK_API_KEY`".
- **`AGENTS.md`** Architecture notes rewritten: `Provider`/`PROVIDER_MAP`
  instead of `Agent`/`AGENT_MAP`; deletion of `executor.py`/`skill.py`/
  `detection.py` reflected; the intentional `except A, B:` note preserved.
- **`action.yml`**: documented to require `SUPERSEDED_DEEPSEEK_API_KEY` as an
  Action secret.

## Versioning

Breaking change: `0.5.0` → `0.6.0`. Pre-1.0 SemVer treats a minor bump as
signalling breaking. No deprecation cycle — keeping the ~1500-line
CLI/sandbox codebase alive for one release would defeat the purpose of the
swap.

## Out of scope / future work

- Additional providers (`OpenAIProvider`, `AnthropicProvider`,
  `LocalVLLMProvider`) — each one class + one `PROVIDER_MAP` line.
- Streaming / SSE from DeepSeek with intra-pass progress.
- CodeRabbit-parity product features (walkthrough summaries, dashboard,
  multi-forge, IDE extensions).
- Tool-calling / multi-step agent loops (current design is single-shot
  prompt → JSON, which is correct for the harness swap but limits what the
  reviewer can verify autonomously).
- Cost guards / per-review budget caps (token usage is now surfaced; a
  budget cap that aborts mid-review is a natural follow-up).
- Prompt-engineering changes that take advantage of the new model
  (e.g. smaller prompts exploiting DeepSeek's context window, or
  chain-of-thought-friendly prompt shapes when `--model deepseek-reasoner`).
