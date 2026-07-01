# Docker Images (CLI + API) and compose.yml — Design

**Date:** 2026-07-01
**Status:** Approved

## Problem

Superseded ships as a Python CLI (`superseded review`, `superseded feedback`) and an
async review server (`superseded serve` — FastAPI/uvicorn webhook receiver + worker).
Today there is a single root `Dockerfile`, purpose-built for the GitHub Action: it
bundles the CLI plus `git`, `gh`, and the three AI CLIs, and runs `entrypoint.sh` to
perform a PR review. There is no image for running the server, and no `compose.yml` to
bring the server (and its Postgres dependency) up locally.

We need:

1. A Docker image to run the **CLI** (`superseded review …`).
2. A Docker image to run the **API** (`superseded serve`).
3. A `compose.yml` that starts the API with a Postgres backend.

## Goals / Non-goals

**Goals**

- Self-contained Dockerfiles that each build with a single `docker build` (no
  prerequisite base image that must be built/tagged first).
- The GitHub Action keeps working after the refactor.
- `docker compose up` brings up a working API backed by Postgres, reading secrets from a
  gitignored `.env` + a mounted private key.
- The server can bind `0.0.0.0` inside a trusted compose network (TLS terminated by an
  upstream reverse proxy) without weakening the existing direct-exposure TLS guard.

**Non-goals**

- Publishing images to a registry (build only).
- A reverse-proxy service inside compose (the proxy runs in front of compose, supplied by
  the operator).
- Multi-arch builds.
- Changing the server's application logic beyond the minimal `behind_proxy` escape hatch.

## Constraints discovered

- **GitHub Actions `using: docker`** builds exactly the image referenced by `runs.image`
  in a single build. It cannot build a prerequisite "base" image that another image
  `FROM`s, and it cannot pass `--target`. So the file referenced by `action.yml` must
  resolve to the CLI image with no flags — i.e. the CLI is the final stage of a
  multi-stage file.
- **`docker compose`** builds each service from one `dockerfile:` (plus an optional
  `target:`). A service whose Dockerfile does `FROM <sibling-base>` is fragile (requires
  the base image to pre-exist in the daemon). A single multi-target file avoids this:
  compose selects the `api` stage with `target: api`.
- **`ServerConfig.require_configured()`** (`src/superseded/server/config.py:44`) rejects
  binding any host other than `127.0.0.1`/`localhost` unless a TLS cert+key are supplied.
  A container binding `127.0.0.1` is unreachable from outside its own network namespace
  (no `ports:` mapping, no sibling container, nothing on the host can reach it). For a
  containerized API to be reachable it must bind `0.0.0.0`, which currently forces TLS
  in-process. The escape hatch below resolves this.
- The server reads its GitHub App **private key from a file** (`SUPERSEDED_PRIVATE_KEY_PATH`)
  and validates the file exists at startup. The key must therefore be mounted into the
  container, not baked into the image.

## Design

### File layout

- `docker/Dockerfile` — **single multi-target** Dockerfile with three named
  stages: `base`, `cli`, `api`. One source of truth for the shared base.
- `docker/entrypoint.sh` — current root `entrypoint.sh` relocated (Action orchestration
  wrapper: bridges `INPUT_*` → `SUPERSEDED_*` and runs `superseded review --pr …`).
- `compose.yml` — API + Postgres.
- `.env.example` — documented env template (actual `.env` is gitignored).
- **Removed:** root `Dockerfile`, root `entrypoint.sh`.

### Why a single multi-target Dockerfile, not base+children or per-target files

The goal is a shared base layer with no duplicated preamble, while keeping every
build path a single command with zero prerequisites. The decisive constraint:
**GitHub Actions `using: docker` builds exactly one image from `runs.image` and
cannot build a prerequisite base or pass `--target`.** Therefore:

- A literal three-file split (`Dockerfile.base` + `Dockerfile.{cli,api}` that
  `FROM superseded-base`) would break the Action unless the base were
  pre-published to a registry (none exists yet).
- A single multi-stage file with named targets satisfies every consumer:
  - base: `docker build -f docker/Dockerfile --target base -t superseded-base .`
  - cli:   `docker build -f docker/Dockerfile --target cli -t superseded-cli .`
  - api:   `docker build -f docker/Dockerfile --target api -t superseded-api .`
  - GitHub Action: `image: docker/Dockerfile` with no target → resolves to the
    **final** stage, so the `cli` stage is deliberately kept last.
  - compose: `build: { dockerfile: docker/Dockerfile, target: api }`.

The base stage holds the shared preamble (apt, gh, node/npm, AI CLIs,
`pip install .`); `cli` and `api` are thin stages `FROM base` that only set
`ENTRYPOINT`/`CMD` (and the CLI stage copies `entrypoint.sh`).

### Dockerfile (single multi-target)

```dockerfile
ARG AI_CLIS="@anthropic-ai/claude-code @openai/codex opencode-ai"

FROM python:3.14-slim AS base
ARG AI_CLIS

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates nodejs npm \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g --no-save \
        ${AI_CLIS} \
    && npm cache clean --force > /dev/null 2>&1 || true

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

# api target (--target api): used by compose.yml. The server talks to GitHub
# over the REST API (httpx) and clones via git; it never invokes `gh`, so gh
# is intentionally NOT installed in this stage.
FROM base AS api
ENTRYPOINT ["superseded"]
CMD ["serve"]

# cli target (default/final stage): used by the GitHub Action and general CLI.
# Kept last so an untargeted build resolves to the CLI image. The local CLI
# path uses `gh pr diff` / `gh pr view`, so gh is installed in this stage only.
FROM base AS cli
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update \
        && apt-get install -y --no-install-recommends gh \
        && rm -rf /var/lib/apt/lists/*
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["superseded"]
CMD ["--help"]
```

Rationale: the server shells out to whichever AI CLI is selected (`ReviewEngine` →
`subprocess.run`), so the agent(s) must be on PATH in both targets. `git` is needed by
both (server clones repos via `server/checkout.py`; CLI uses git for diffs). `gh` is
needed **only by the local CLI** (`diff.py`: `gh pr diff`, `gh pr view`); the server
uses the GitHub REST API via `httpx` and never invokes `gh`, so `gh` lives in the `cli`
stage only. `AI_CLIS` (a build arg, overridable via `--build-arg` or compose
`build.args`/`.env`) defaults to all three agents; set it to a single agent to build a
slim image (~1 GB instead of ~2 GB).

### `docker/entrypoint.sh`

Identical to today's root `entrypoint.sh`, relocated. No logic changes. It reads
`INPUT_AGENT`/`INPUT_MODEL`/`INPUT_PASSES`/`INPUT_POST` and
`GITHUB_EVENT_PULL_REQUEST_NUMBER` (all forwarded by `action.yml` env), validates the
selected AI CLI is on PATH, and `exec`s `superseded review --pr "$PR_NUMBER" …`.

### GitHub Action wiring

`action.yml` `runs:` changes from:

```yaml
runs:
  using: "docker"
  image: "Dockerfile"
  env: { ... }
```

to:

```yaml
runs:
  using: "docker"
  image: "docker/Dockerfile"
  entrypoint: "/entrypoint.sh"
  env: { ... }
```

`image: docker/Dockerfile` builds with no target → resolves to the final stage,
which is `cli`. The `runs.entrypoint` field overrides the stage's `ENTRYPOINT`
for the duration of the Action run. `env:` is unchanged. `entrypoint.sh`
behavior is unchanged, so Action behavior is identical; only paths move.

### The `behind_proxy` escape hatch (code change)

The only source-code change. In `src/superseded/server/config.py`:

- Add field: `behind_proxy: bool = False`.
- `from_env()` reads `SUPERSEDED_BEHIND_PROXY` (truthy `"1"`/`"true"`/`"yes"`/`"on"` →
  `True`), mirroring `resolve_graph`'s parsing in `cli.py:78`.
- `require_configured()` is updated: the TLS-for-non-loopback check is skipped **only
  when** `behind_proxy` is `True`. When skipped, the CLI startup path logs a warning that
  the operator asserts TLS terminates upstream. Direct binds (the default) keep the strict
  guard unchanged.

Pseudo-diff for `require_configured`:

```python
if self.host not in ("127.0.0.1", "localhost") and not (
    self.tls_cert_path and self.tls_key_path
) and not self.behind_proxy:
    raise ValueError(
        f"Binding to {self.host} requires TLS (or set SUPERSEDED_BEHIND_PROXY=1 "
        "when TLS terminates at a trusted upstream reverse proxy). Set "
        "SUPERSEDED_TLS_CERT/SUPERSEDED_TLS_KEY, use --host 127.0.0.1, "
        "or enable behind-proxy mode."
    )
```

This is intentionally narrow: it relaxes one specific guard, opt-in, for the
trusted-network scenario, and the error message now points operators at the escape hatch.

### `compose.yml`

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
      dockerfile: docker/Dockerfile
      target: api
      args:
        AI_CLIS: ${AI_CLIS:-@anthropic-ai/claude-code @openai/codex opencode-ai}
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

Notes:

- **No `ports:` on `api`.** The API binds `0.0.0.0:8000` inside the compose network only;
  an operator-supplied reverse proxy (nginx/caddy/traefik, on the host or as a separate
  deployment) terminates TLS and forwards to the compose service. This matches the chosen
  architecture. To reach the API directly for local debugging, an operator can add
  `ports: ["8000:8000"]` temporarily (still requires `SUPERSEDED_BEHIND_PROXY=1`, since
  the container binds `0.0.0.0`).
- `:?` required-var syntax makes compose fail fast with a clear message if `.env` is
  missing a required secret, instead of booting a misconfigured server.
- `./keys:/keys:ro` mounts the GitHub App private key read-only; it is never baked into
  the image and never present in `.env`.
- `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` are forwarded so the in-image AI CLIs can
  authenticate when the server shells out to them.
- `superseded-tmp` volume persists repo checkouts across restarts and avoids writing into
  the container's writable layer.

### `.env.example`

A committed template documenting every variable `compose.yml` reads. The actual `.env` is
gitignored. Contents:

```
# Postgres
POSTGRES_USER=superseded
POSTGRES_PASSWORD=change-me
POSTGRES_DB=superseded

# GitHub App (server mode)
SUPERSEDED_APP_ID=123456
SUPERSEDED_WEBHOOK_SECRET=change-me
# Private key is mounted from ./keys/private-key.pem (not stored in .env)

# Server review defaults (optional)
SUPERSEDED_LOG_LEVEL=info
SUPERSEDED_SERVER_AGENT=
SUPERSEDED_SERVER_MODEL=

# AI CLI credentials (optional, depends on chosen agent)
ANTHROPIC_API_KEY=
OPENAI_API_KEY=

# Optional: slim the images by installing only the AI CLI(s) you run.
# Space-separated npm package names. Default (unset) installs all three.
# AI_CLIS=@anthropic-ai/claude-code
```

### gitignore & dockerignore updates

- `.gitignore`: add `keys/`, `.env`.
- `.dockerignore`: add `keys/`, `*.pem`, `compose.yml`, `.env`, `.env.example` (none of
  these belong in either image).

### Data flow

```
GitHub webhook ──TLS──▶ operator reverse proxy ──HTTP──▶ compose:api:8000
                                                                    │
                                  ┌─────────────────────────────────┤
                                  ▼                                 ▼
                       worker shells out to               PostgresStore ──▶ db (postgres)
                       claude/codex/opencode
                       (ANTHROPIC/OPENAI keys from env)
                                  │
                                  ▼
                       gh API (git clone, review post) — authenticated via GitHub App JWT
```

### Error handling

- `compose.yml` `:?` required vars fail fast on missing secrets.
- `db` healthcheck gates `api` startup (`depends_on.condition: service_healthy`), so the
  server never boots before Postgres accepts connections.
- `SUPERSEDED_BEHIND_PROXY=1` without an actual upstream proxy is an operator error (the
  webhook receiver would be exposed plaintext); the startup warning makes this explicit.

## Testing

- **Unit:** `require_configured()` accepts `host="0.0.0.0"` when `behind_proxy=True` and
  rejects it (unchanged) when `False`.
- **Unit:** `from_env()` parses `SUPERSEDED_BEHIND_PROXY` (truthy/falsey variants).
- **Existing:** `uv run pytest tests/ -v` stays green (the config change is additive).
- **Lint/format:** `uv run ruff check src/ tests/` and `uv run ruff format src/ tests/`.
- **Manual smoke (not automated, no CI):**
  - `docker build -f docker/Dockerfile --target cli -t superseded-cli .` then
    `docker run --rm superseded-cli --version`.
  - `docker build -f docker/Dockerfile --target api -t superseded-api .` then
    `docker run --rm -e SUPERSEDED_APP_ID=0 superseded-api serve` (expect the existing
    "not configured" exit, proving the image runs).
  - `docker build -f docker/Dockerfile --target base -t superseded-base .` (base builds).
  - `cp .env.example .env` (fill values) + place a key in `./keys/private-key.pem` +
    `docker compose up` (expect healthy db + api listening on the compose network).

## Resolved questions

- **GitHub Actions `runs.entrypoint`** for `using: docker` actions is supported
  (confirmed against the current metadata reference: it "Overrides the Docker
  ENTRYPOINT in the Dockerfile"). Also: a local image file must be named
  `Dockerfile`, which is why the single file is `docker/Dockerfile`.
