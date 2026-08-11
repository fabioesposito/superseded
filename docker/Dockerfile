# Multi-target Dockerfile.
#
# Build the base layer once and reuse it for the CLI and API targets:
#
#   docker build -f docker/Dockerfile --target base -t superseded-base .
#   docker build -f docker/Dockerfile --target cli   -t superseded-cli  .
#   docker build -f docker/Dockerfile --target api   -t superseded-api  .
#
# compose.yml builds the `api` target. The GitHub Action is a composite client
# that POSTs to a running server; it no longer builds this image. The `cli`
# stage is kept last on purpose so an untargeted build resolves to the CLI
# image. Do not reorder the stages.

FROM python:3.14-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

# --- API target (built by compose.yml via --target api) ---
# The server talks to GitHub over the REST API (httpx) and clones repos via git;
# it never invokes `gh`, so gh is intentionally NOT installed here.
FROM base AS api
ENTRYPOINT ["superseded"]
CMD ["serve"]

# --- CLI target (default/final stage; used for containerized CLI use) ---
# Kept last so an untargeted build produces the CLI image. The local CLI path
# uses `gh pr diff` / `gh pr view`, so gh is installed in this stage only.
FROM base AS cli
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update \
        && apt-get install -y --no-install-recommends gh \
        && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["superseded"]
CMD ["--help"]
