# Multi-target Dockerfile.
#
# Build the base layer once and reuse it for the CLI and API targets:
#
#   # full images (all three AI CLIs):
#   docker build -f docker/Dockerfile --target base -t superseded-base .
#   docker build -f docker/Dockerfile --target cli   -t superseded-cli  .
#   docker build -f docker/Dockerfile --target api   -t superseded-api  .
#
#   # slim variant (one agent only):
#   docker build -f docker/Dockerfile --build-arg AI_CLIS=@anthropic-ai/claude-code \
#       --target cli -t superseded-cli-claude .
#
# compose.yml builds the `api` target (override AI_CLIS via .env or
# --build-arg). The GitHub Action builds `docker/Dockerfile` with no --target,
# so it resolves to the FINAL stage; the `cli` stage is therefore kept last on
# purpose. Do not reorder the stages.

# Space-separated npm package specs of the AI CLI(s) to install. Override with
# --build-arg (or compose build.args / .env AI_CLIS) to build a slim image that
# carries only the agent you actually run.
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

# --- API target (built by compose.yml via --target api) ---
# The server talks to GitHub over the REST API (httpx) and clones repos via git;
# it never invokes `gh`, so gh is intentionally NOT installed here.
FROM base AS api
ENTRYPOINT ["superseded"]
CMD ["serve"]

# --- CLI target (default/final stage; used by the GitHub Action and general CLI use) ---
# Kept last so an untargeted build produces the CLI image. The local CLI path
# uses `gh pr diff` / `gh pr view`, so gh is installed in this stage only. The
# GitHub Action overrides ENTRYPOINT via action.yml `runs.entrypoint: /entrypoint.sh`.
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
