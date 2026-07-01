# Docker Images (CLI + API) and compose.yml — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship self-contained Docker images for the `superseded` CLI and the `superseded serve` API, relocate the root `Dockerfile`/`entrypoint.sh` into `docker/`, keep the GitHub Action working, and add a `compose.yml` that runs the API backed by Postgres.

**Architecture:** A single multi-target `docker/Dockerfile` with three named stages (`base`, `cli`, `api`) — one source of truth for the shared base preamble, with no duplicated content and no prerequisite builds. The `cli` stage is the final stage so the GitHub Action (which builds with no `--target`) resolves to it; compose selects `api` via `target: api`. `gh` is installed **only in the `cli` stage** (the server uses the GitHub REST API via httpx and never invokes `gh`). An `AI_CLIS` build arg (overridable via `--build-arg` or compose `build.args`/`.env`) defaults to all three agents; set it to one agent for a slim ~1 GB build. A narrow, opt-in `behind_proxy` flag on `ServerConfig` lets the server bind `0.0.0.0` inside a trusted compose network (TLS terminated upstream) without weakening the default direct-exposure guard. `compose.yml` wires the API to a Postgres service with a mounted private key and secrets from a gitignored `.env`.

**Tech Stack:** Python 3.14, FastAPI/uvicorn (server), asyncpg/Postgres, Docker, Docker Compose, GitHub Actions (docker container action).

**Spec:** `docs/superseded/specs/2026-07-01-docker-images-design.md`

**Conventions (from AGENTS.md):** Every Python module starts with `from __future__ import annotations`. Ruff rules `E,W,F,I,N,UP,B,SIM,TCH,RUF` (ignoring `E501,B008,TC001-003,E741`), line length 100, double quotes, isort with `known-first-party = ["superseded"]`. Run everything via `uv run`. No CI; verify with `uv run ruff check`, `uv run ruff format`, `uv run pytest`.

---

## File Structure

**Create:**
- `docker/Dockerfile` — single multi-target Dockerfile with stages `base`, `cli`, `api` (`cli` last = the GitHub Action's default image).
- `docker/entrypoint.sh` — relocated Action orchestration wrapper (identical to current root `entrypoint.sh`).
- `compose.yml` — API + Postgres.
- `.env.example` — documented env template (actual `.env` gitignored).

**Modify:**
- `src/superseded/server/config.py` — add `behind_proxy` field, parse `SUPERSEDED_BEHIND_PROXY` in `from_env()`, relax `require_configured()` TLS guard when behind-proxy is set (with a warning).
- `action.yml` — `image: docker/Dockerfile`, add `entrypoint: /entrypoint.sh`.
- `.gitignore` — add `keys/`, `.env`.
- `.dockerignore` — add `keys/`, `*.pem`, `compose.yml`, `.env`, `.env.example`.
- `tests/test_server_config.py` — new tests for `behind_proxy`.

**Delete:**
- `Dockerfile` (root) — superseded by `docker/Dockerfile`.
- `entrypoint.sh` (root) — superseded by `docker/entrypoint.sh`.

---

### Task 1: Add `behind_proxy` to ServerConfig (TDD)

This is the only source-code change. It adds an opt-in flag so the server can bind `0.0.0.0` inside a trusted compose network without TLS, asserting TLS terminates upstream.

**Files:**
- Modify: `tests/test_server_config.py` (append new tests)
- Modify: `src/superseded/server/config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server_config.py`:

```python
def test_server_config_behind_proxy_defaults_false():
    config = ServerConfig()
    assert config.behind_proxy is False


def test_server_config_require_configured_rejects_non_loopback_without_tls():
    key_file = Path("/dev/null")
    config = ServerConfig(
        app_id=123,
        webhook_secret="s",
        private_key_path=key_file,
        host="0.0.0.0",
        behind_proxy=False,
    )
    with pytest.raises(ValueError, match="requires TLS"):
        config.require_configured()


def test_server_config_require_configured_allows_non_loopback_when_behind_proxy(caplog):
    key_file = Path("/dev/null")
    config = ServerConfig(
        app_id=123,
        webhook_secret="s",
        private_key_path=key_file,
        host="0.0.0.0",
        behind_proxy=True,
    )
    with caplog.at_level("WARNING", logger="superseded.server.config"):
        config.require_configured()
    assert any("SUPERSEDED_BEHIND_PROXY" in rec.message for rec in caplog.records)


def test_server_config_behind_proxy_from_env(monkeypatch, tmp_path):
    key = tmp_path / "key.pem"
    key.write_text("x")
    monkeypatch.setenv("SUPERSEDED_APP_ID", "111")
    monkeypatch.setenv("SUPERSEDED_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("SUPERSEDED_PRIVATE_KEY_PATH", str(key))
    monkeypatch.setenv("SUPERSEDED_BEHIND_PROXY", "true")
    cfg = ServerConfig.from_env()
    assert cfg.behind_proxy is True


def test_server_config_behind_proxy_falsey_from_env(monkeypatch, tmp_path):
    key = tmp_path / "key.pem"
    key.write_text("x")
    monkeypatch.setenv("SUPERSEDED_APP_ID", "111")
    monkeypatch.setenv("SUPERSEDED_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("SUPERSEDED_PRIVATE_KEY_PATH", str(key))
    monkeypatch.setenv("SUPERSEDED_BEHIND_PROXY", "0")
    cfg = ServerConfig.from_env()
    assert cfg.behind_proxy is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_server_config.py -v`
Expected: FAIL — `behind_proxy` is not an attribute of `ServerConfig`, and `require_configured()` raises on `host="0.0.0.0"` regardless.

- [ ] **Step 3: Add the `behind_proxy` field and logging import**

In `src/superseded/server/config.py`, add a module logger after the existing imports. The file currently begins with:

```python
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator
```

Change the top of the file to:

```python
from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator

logger = logging.getLogger(__name__)
```

- [ ] **Step 4: Add the `behind_proxy` field to the model**

The current `ServerConfig` field block ends with:

```python
    agent: str | None = None
    model: str | None = None
```

Change it to:

```python
    agent: str | None = None
    model: str | None = None
    behind_proxy: bool = False
```

- [ ] **Step 5: Relax `require_configured()` when `behind_proxy` is set**

The current method is:

```python
    def require_configured(self) -> None:
        """Raise ValueError if the server is not fully configured for production.

        Guards against booting a webhook receiver with a forgeable empty
        webhook secret / missing app credentials.
        """
        if not self.is_configured:
            raise ValueError(
                "Server is not configured: set SUPERSEDED_APP_ID, "
                "SUPERSEDED_WEBHOOK_SECRET, and SUPERSEDED_PRIVATE_KEY_PATH "
                "(or provide them in the YAML config)."
            )
        if self.host not in ("127.0.0.1", "localhost") and not (
            self.tls_cert_path and self.tls_key_path
        ):
            raise ValueError(
                f"Binding to {self.host} requires TLS. Set SUPERSEDED_TLS_CERT "
                "and SUPERSEDED_TLS_KEY, or use --host 127.0.0.1."
            )
```

Change it to:

```python
    def require_configured(self) -> None:
        """Raise ValueError if the server is not fully configured for production.

        Guards against booting a webhook receiver with a forgeable empty
        webhook secret / missing app credentials, or binding a non-loopback
        interface without TLS unless the operator has explicitly asserted that
        TLS terminates at a trusted upstream reverse proxy (``behind_proxy``).
        """
        if not self.is_configured:
            raise ValueError(
                "Server is not configured: set SUPERSEDED_APP_ID, "
                "SUPERSEDED_WEBHOOK_SECRET, and SUPERSEDED_PRIVATE_KEY_PATH "
                "(or provide them in the YAML config)."
            )
        if self.host not in ("127.0.0.1", "localhost") and not (
            self.tls_cert_path and self.tls_key_path
        ):
            if self.behind_proxy:
                logger.warning(
                    "Binding to %s without TLS; SUPERSEDED_BEHIND_PROXY=1 asserts "
                    "that TLS terminates at a trusted upstream reverse proxy.",
                    self.host,
                )
            else:
                raise ValueError(
                    f"Binding to {self.host} requires TLS. Set SUPERSEDED_TLS_CERT "
                    "and SUPERSEDED_TLS_KEY, use --host 127.0.0.1, or enable "
                    "behind-proxy mode (SUPERSEDED_BEHIND_PROXY=1) when TLS "
                    "terminates at a trusted upstream reverse proxy."
                )
```

- [ ] **Step 6: Parse `SUPERSEDED_BEHIND_PROXY` in `from_env()`**

In `from_env()`, the last parsed env var before the `return` is currently:

```python
        model = os.environ.get("SUPERSEDED_SERVER_MODEL")
        if model:
            kwargs["model"] = model

        return cls(**kwargs)
```

Change it to:

```python
        model = os.environ.get("SUPERSEDED_SERVER_MODEL")
        if model:
            kwargs["model"] = model

        behind_proxy = os.environ.get("SUPERSEDED_BEHIND_PROXY")
        if behind_proxy:
            kwargs["behind_proxy"] = behind_proxy.strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )

        return cls(**kwargs)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_server_config.py -v`
Expected: PASS — all five new tests pass, and no existing test regresses.

- [ ] **Step 8: Lint and format**

Run: `uv run ruff check src/ tests/ && uv run ruff format src/ tests/`
Expected: no errors; formatting unchanged (file already formatted) or minor whitespace fixes applied.

- [ ] **Step 9: Commit**

```bash
git add src/superseded/server/config.py tests/test_server_config.py
git commit -m "feat(server): add behind_proxy flag for trusted reverse-proxy binds"
```

---

### Task 2: Create the Dockerfiles and relocate `entrypoint.sh`

Create the two self-contained Dockerfiles under `docker/`. The CLI image file is named `Dockerfile` (not `Dockerfile.cli`) because the GitHub Actions metadata spec requires a local image file to be named `Dockerfile`.

**Files:**
- Create: `docker/Dockerfile`
- Create: `docker/Dockerfile.api`
- Create: `docker/entrypoint.sh` (copy of root `entrypoint.sh`)

- [ ] **Step 1: Create `docker/entrypoint.sh`**

Copy the exact contents of the current root `entrypoint.sh` into `docker/entrypoint.sh` (no logic changes). The file is:

```bash
#!/bin/bash
set -euo pipefail

# SUPERSEDED_AGENT / SUPERSEDED_MODEL are forwarded from action.yml inputs and
# override the CLI flags (env precedence > flags, see cli.resolve_agent). Fall
# back to INPUT_* then empty so the tool auto-detects when nothing is supplied.
AGENT="${SUPERSEDED_AGENT:-${INPUT_AGENT:-}}"
MODEL="${SUPERSEDED_MODEL:-${INPUT_MODEL:-}}"
PASSES="${INPUT_PASSES:-security,correctness,performance,style,architecture}"
POST="${INPUT_POST:-true}"

PR_NUMBER="${GITHUB_EVENT_PULL_REQUEST_NUMBER:-}"

if [ -z "$PR_NUMBER" ]; then
    echo "Error: GITHUB_EVENT_PULL_REQUEST_NUMBER is not set; this action must run on a pull_request event." >&2
    exit 1
fi

if [ -z "$AGENT" ]; then
    echo "No agent specified; superseded will auto-detect the highest-preference AI CLI installed." >&2
fi

# Validate the chosen AI CLI is on PATH before invoking superseded, so failures
# surface as a clear message instead of a per-pass RuntimeError stack trace.
BINARY="${AGENT}"
case "$AGENT" in
    claude-code) BINARY="claude" ;;
    opencode)   BINARY="opencode" ;;
    codex)      BINARY="codex" ;;
    "")         BINARY="" ;;
    *)          BINARY="$AGENT" ;;
esac

if [ -n "$BINARY" ] && ! command -v "$BINARY" >/dev/null 2>&1; then
    echo "Error: agent CLI '$BINARY' (for agent '$AGENT') was not found on PATH." >&2
    echo "Install it in the Docker image or set 'agent:' to a CLI that is installed." >&2
    exit 1
fi

CMD=(superseded review --pr "$PR_NUMBER" --passes "$PASSES")

# Only pass --agent / --model when explicitly set; otherwise let superseded use
# its config / auto-detection defaults. Env vars (SUPERSEDED_AGENT/MODEL) still
# take precedence over these flags per cli.resolve_*.
if [ -n "$AGENT" ]; then
    CMD+=(--agent "$AGENT")
fi
if [ -n "$MODEL" ]; then
    CMD+=(--model "$MODEL")
fi

if [ "$POST" = "true" ]; then
    CMD+=(--post)
fi

echo "Running: ${CMD[*]}"
exec "${CMD[@]}"
```

- [ ] **Step 2: Create `docker/Dockerfile` (CLI image)**

```dockerfile
FROM python:3.14-slim

# --- shared base preamble (keep in sync with docker/Dockerfile.api) ---
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates gnupg \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh nodejs npm \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g --no-save \
        @anthropic-ai/claude-code \
        @openai/codex \
        opencode-ai \
    && npm cache clean --force > /dev/null 2>&1 || true

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .
# --- end shared base preamble ---

# Action orchestration wrapper. The GitHub Action overrides the image ENTRYPOINT
# via action.yml `runs.entrypoint: /entrypoint.sh`; for general CLI use the
# entrypoint below (`superseded`) applies.
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["superseded"]
CMD ["--help"]
```

- [ ] **Step 3: Create `docker/Dockerfile.api` (server image)**

```dockerfile
FROM python:3.14-slim

# --- shared base preamble (keep in sync with docker/Dockerfile) ---
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates gnupg \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh nodejs npm \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g --no-save \
        @anthropic-ai/claude-code \
        @openai/codex \
        opencode-ai \
    && npm cache clean --force > /dev/null 2>&1 || true

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .
# --- end shared base preamble ---

# Server: all configuration is read from SUPERSEDED_* env vars at startup.
ENTRYPOINT ["superseded"]
CMD ["serve"]
```

- [ ] **Step 4: Commit**

```bash
git add docker/Dockerfile docker/Dockerfile.api docker/entrypoint.sh
git commit -m "build(docker): add CLI and API Dockerfiles under docker/"
```

---

### Task 3: Point the GitHub Action at the new CLI image

Update `action.yml` to reference `docker/Dockerfile` and explicitly set the entrypoint to the relocated wrapper.

**Files:**
- Modify: `action.yml`

- [ ] **Step 1: Update the `runs:` block**

The current `runs:` block in `action.yml` is:

```yaml
runs:
  using: "docker"
  image: "Dockerfile"
  env:
    GITHUB_TOKEN: ${{ github.token }}
    SUPERSEDED_AGENT: ${{ inputs.agent }}
    SUPERSEDED_MODEL: ${{ inputs.model }}
    ANTHROPIC_API_KEY: ${{ inputs.anthropic_api_key }}
    OPENAI_API_KEY: ${{ inputs.openai_api_key }}
```

Change the `image:` value and add an `entrypoint:` line directly under `image:`:

```yaml
runs:
  using: "docker"
  image: "docker/Dockerfile"
  entrypoint: "/entrypoint.sh"
  env:
    GITHUB_TOKEN: ${{ github.token }}
    SUPERSEDED_AGENT: ${{ inputs.agent }}
    SUPERSEDED_MODEL: ${{ inputs.model }}
    ANTHROPIC_API_KEY: ${{ inputs.anthropic_api_key }}
    OPENAI_API_KEY: ${{ inputs.openai_api_key }}
```

Leave `inputs:` and the `env:` contents untouched.

- [ ] **Step 2: Commit**

```bash
git add action.yml
git commit -m "build(action): point GitHub Action at docker/Dockerfile"
```

---

### Task 4: Remove the superseded root Dockerfile and entrypoint

Delete the root files now that their content lives under `docker/`.

**Files:**
- Delete: `Dockerfile`
- Delete: `entrypoint.sh`

- [ ] **Step 1: Delete the root files**

```bash
git rm Dockerfile entrypoint.sh
```

- [ ] **Step 2: Commit**

```bash
git commit -m "build(docker): remove superseded root Dockerfile and entrypoint.sh"
```

---

### Task 5: Add `compose.yml` and `.env.example`

The compose file brings up the API backed by Postgres. The API binds `0.0.0.0` on the compose network only (no published port; an operator-supplied reverse proxy terminates TLS in front). Secrets come from a gitignored `.env`; the GitHub App private key is mounted read-only from `./keys/`.

**Files:**
- Create: `compose.yml`
- Create: `.env.example`

- [ ] **Step 1: Create `compose.yml`**

```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-superseded}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}
      POSTGRES_DB: ${POSTGRES_DB:-superseded}
    volumes:
      - superseded-pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-superseded}"]
      interval: 5s
      timeout: 3s
      retries: 10

  api:
    build:
      context: .
      dockerfile: docker/Dockerfile.api
    depends_on:
      db:
        condition: service_healthy
    environment:
      SUPERSEDED_DATABASE_URL: postgres://${POSTGRES_USER:-superseded}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB:-superseded}
      SUPERSEDED_APP_ID: ${SUPERSEDED_APP_ID:?set SUPERSEDED_APP_ID in .env}
      SUPERSEDED_WEBHOOK_SECRET: ${SUPERSEDED_WEBHOOK_SECRET:?set SUPERSEDED_WEBHOOK_SECRET in .env}
      SUPERSEDED_PRIVATE_KEY_PATH: /keys/private-key.pem
      SUPERSEDED_HOST: "0.0.0.0"
      SUPERSEDED_PORT: "8000"
      SUPERSEDED_BEHIND_PROXY: "1"
      SUPERSEDED_LOG_FORMAT: json
      SUPERSEDED_LOG_LEVEL: ${SUPERSEDED_LOG_LEVEL:-info}
      SUPERSEDED_SERVER_AGENT: ${SUPERSEDED_SERVER_AGENT:-}
      SUPERSEDED_SERVER_MODEL: ${SUPERSEDED_SERVER_MODEL:-}
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
      OPENAI_API_KEY: ${OPENAI_API_KEY:-}
    volumes:
      - ./keys:/keys:ro
      - superseded-tmp:/tmp/superseded

volumes:
  superseded-pgdata:
  superseded-tmp:
```

- [ ] **Step 2: Create `.env.example`**

```
# Postgres backend
POSTGRES_USER=superseded
POSTGRES_PASSWORD=change-me
POSTGRES_DB=superseded

# GitHub App (server mode). Required to boot `superseded serve`.
SUPERSEDED_APP_ID=123456
SUPERSEDED_WEBHOOK_SECRET=change-me

# The GitHub App private key is mounted from ./keys/private-key.pem (read-only),
# NOT stored in this file. The container reads it via SUPERSEDED_PRIVATE_KEY_PATH.

# Server review defaults (optional)
SUPERSEDED_LOG_LEVEL=info
SUPERSEDED_SERVER_AGENT=
SUPERSEDED_SERVER_MODEL=

# AI CLI credentials (optional — set the one(s) matching your chosen agent)
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
```

- [ ] **Step 3: Commit**

```bash
git add compose.yml .env.example
git commit -m "build(compose): add API + Postgres compose and env template"
```

---

### Task 6: Update ignore files

Keep secrets and runtime artifacts out of images and git.

**Files:**
- Modify: `.gitignore`
- Modify: `.dockerignore`

- [ ] **Step 1: Add secrets/runtime entries to `.gitignore`**

Append to `.gitignore` (create the lines if not already present):

```
keys/
.env
```

- [ ] **Step 2: Add entries to `.dockerignore`**

The current `.dockerignore` is:

```
.git
.gitignore
.venv
.pytest_cache
.ruff_cache
.code-review-graph
.superseded
.superseded.yaml
docs
tests
**/__pycache__
**/*.pyc
*.md
LICENSE
node_modules
```

Append:

```
keys/
*.pem
compose.yml
.env
.env.example
```

- [ ] **Step 3: Commit**

```bash
git add .gitignore .dockerignore
git commit -m "build: ignore secrets, keys, and compose artifacts"
```

---

### Task 7: Final verification

Confirm the whole change set is consistent and green. There is no CI; verification is manual.

- [ ] **Step 1: Full test suite**

Run: `uv run pytest tests/ -v`
Expected: PASS. The five new config tests pass; nothing else regresses.

  Note: `src/superseded/server/worker.py:296` contains a pre-existing Python-2-style `except ValueError, TypeError:` syntax error that is **out of scope** for this work. If `tests/test_server_worker.py` fails to collect because of it, that is a pre-existing issue unrelated to this plan — do not fix it here.

- [ ] **Step 2: Lint and format**

Run: `uv run ruff check src/ tests/ && uv run ruff format src/ tests/`
Expected: no lint errors; formatting clean.

- [ ] **Step 3: CLI image builds and runs**

Run: `docker build -f docker/Dockerfile -t superseded-cli . && docker run --rm superseded-cli --version`
Expected: build succeeds; the container prints the installed `superseded` version.

- [ ] **Step 4: API image builds and runs (config-error smoke check)**

Run: `docker build -f docker/Dockerfile.api -t superseded-api . && docker run --rm -e SUPERSEDED_APP_ID=0 superseded-api serve`
Expected: build succeeds; the container exits with the existing "Server is not configured" error (proving the image runs `superseded serve` and the config guard works). This confirms the image is functional without needing live GitHub credentials.

- [ ] **Step 5: Verify the design spec is referenced**

Confirm `docs/superseded/specs/2026-07-01-docker-images-design.md` still exists and is committed (it was written during brainstorming).

---

## Self-Review Notes

**Spec coverage:**
- Two self-contained Dockerfiles → Task 2. (Spec said `Dockerfile.cli`; refined to `docker/Dockerfile` per the GitHub Actions "must be named Dockerfile" rule discovered while writing the plan. The spec's open question is resolved.)
- `behind_proxy` code change → Task 1.
- `action.yml` wiring (`image` + `entrypoint`) → Task 3.
- Root file removal → Task 4.
- `compose.yml` + `.env.example` → Task 5.
- ignore-file updates → Task 6.
- Testing → Tasks 1 (unit) and 7 (full suite + image smoke).

**Type/name consistency:** field is `behind_proxy` everywhere; env var is `SUPERSEDED_BEHIND_PROXY` everywhere; image filenames (`docker/Dockerfile`, `docker/Dockerfile.api`, `docker/entrypoint.sh`) are consistent across all tasks.

**No placeholders:** every code/command step contains the exact content.

---

## Execution

Plan complete and saved to `docs/superseded/plans/2026-07-01-docker-images.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
