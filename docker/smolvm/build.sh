#!/usr/bin/env bash
# Build slim per-agent OCI images for smolvm-sandboxed reviews. Each image
# contains just the named agent CLI + sh + runtime deps; superseded itself
# stays on the host. The build context is this directory (no repo source
# needed) so builds are fast and cache-friendly.
#
# Usage:
#   docker/smolvm/build.sh                  # build claude + opencode + codex
#   docker/smolvm/build.sh claude           # one agent only
#   docker/smolvm/build.sh claude v1.0      # one agent with a custom tag suffix
#
# After building, point the server at the image(s):
#   export SUPERSEDED_SMOLVM_IMAGE_CLAUDE=superseded-smolvm-claude:latest
#   export SUPERSEDED_SANDBOX_KIND=smolvm
#   superseded serve
set -euo pipefail

AGENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

AGENTS=("claude" "opencode" "codex")
if [[ $# -ge 1 ]]; then
    AGENTS=("$1")
fi
TAG_SUFFIX="${2:-latest}"

for short in "${AGENTS[@]}"; do
    case "${short}" in
        claude)  agent_pkg="claude-code" ;;
        opencode) agent_pkg="opencode" ;;
        codex)   agent_pkg="codex" ;;
        *)
            echo "Unknown agent: ${short} (expected claude|opencode|codex)" >&2
            exit 1
            ;;
    esac
    tag="superseded-smolvm-${short}:${TAG_SUFFIX}"
    echo "→ Building ${tag} (npm package: ${agent_pkg})"
    docker build -f "${AGENT_DIR}/Dockerfile" \
        --build-arg AGENT="${agent_pkg}" \
        -t "${tag}" \
        "${AGENT_DIR}"
    echo "✓ ${tag}"
done

echo
echo "Done. Configure the server to use the image(s):"
for short in "${AGENTS[@]}"; do
    upper="$(echo "${short}" | tr '[:lower:]' '[:upper:]')"
    echo "  export SUPERSEDED_SMOLVM_IMAGE_${upper}=superseded-smolvm-${short}:${TAG_SUFFIX}"
done
echo "  export SUPERSEDED_SANDBOX=1"
echo "  export SUPERSEDED_SANDBOX_KIND=smolvm"
