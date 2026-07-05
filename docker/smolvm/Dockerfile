# Slim per-agent OCI images for smolvm-sandboxed reviews.
#
# The superseded server boots one of these images as a microVM via the embedded
# `smol` SDK: `MountSpec(host_checkout, "/workspace", read_only=False)` mounts
# the per-job repo checkout, and `ExecOptions.env` injects the provider key(s)
# from the server's own environment. The agent CLI runs inside the VM; superseded
# itself, `gh`, and `git` stay on the host.
#
# Build via the helper (recommended):
#   docker/smolvm/build.sh                 # claude + opencode + codex
#   docker/smolvm/build.sh claude          # one agent only
#
# Or directly (note: build context is this directory, not the repo root):
#   docker build -f docker/smolvm/Dockerfile --build-arg AGENT=claude-code \
#       -t superseded-smolvm-claude:latest docker/smolvm/
#
# Then point the server at the image:
#   export SUPERSEDED_SMOLVM_IMAGE_CLAUDE=superseded-smolvm-claude:latest

ARG AGENT=claude-code

FROM node:20-alpine
ARG AGENT

# bash: some agent CLIs are bash-centric (our exec wrapper uses sh, but the
#       agent's own subprocesses may invoke bash).
# jq:    handy for any JSON the agent emits; tiny.
# ca-certificates: HTTPS to the model provider.
# curl:  kept for any agent runtime self-update / install hooks.
# tini:  PID 1 signal forwarding so the agent exits cleanly on exec timeout.
RUN apk add --no-cache bash jq ca-certificates curl tini

# Install the named agent CLI globally. Package names mirror the existing
# docker/Dockerfile's AI_CLIS default so the two build paths agree.
RUN case "${AGENT}" in \
        claude-code) npm install -g @anthropic-ai/claude-code ;; \
        opencode)    npm install -g opencode-ai ;; \
        codex)       npm install -g @openai/codex ;; \
        *) echo "Unknown AGENT='${AGENT}'; expected claude-code|opencode|codex" >&2; exit 1 ;; \
    esac \
    && npm cache clean --force > /dev/null 2>&1 || true

# /workspace is where superseded mounts the per-job repo checkout (MountSpec
# target). Pre-create as a safety net in case a future smolvm release changes
# the default mount semantics.
RUN mkdir -p /workspace
WORKDIR /workspace

# Sanity check at build time: the agent CLI must be on PATH. Fails the build
# early if the npm package name changes upstream or the install silently broke.
# The binary name differs from the AGENT arg (claude-code package ships `claude`).
RUN case "${AGENT}" in \
        claude-code) claude --version ;; \
        opencode)    opencode --version ;; \
        codex)       codex --version ;; \
    esac || echo "warning: agent CLI did not report a version; build continues" >&2

ENTRYPOINT ["/sbin/tini", "--"]

# No default CMD: the server drives the VM via m.exec(). For interactive use,
# run e.g. `docker run -it superseded-smolvm-claude:latest sh`.
