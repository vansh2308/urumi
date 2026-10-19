#!/usr/bin/env bash
# Build the four images for the VPS architecture and push them to a registry.
#
#   REGISTRY=ghcr.io/your-org deploy/scripts/push-images.sh       # tag = current commit
#
# Log in first (`docker login ghcr.io`). Images are named <REGISTRY>/urumi-<service>:<TAG>,
# matching values-production.yaml. Refuses a dirty tree, so a tag always means one commit.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REGISTRY="${REGISTRY:?set REGISTRY, e.g. ghcr.io/your-org}"
PLATFORM="${PLATFORM:-linux/amd64}"
TAG="${TAG:-$(git -C "${ROOT}" rev-parse --short HEAD)}"
if [[ -n "$(git -C "${ROOT}" status --porcelain -- services apps)" ]]; then
  echo "uncommitted changes in services/ or apps/: commit before pushing ${TAG}" >&2
  exit 1
fi

for entry in woocommerce-mcp:services/woocommerce-mcp gateway:services/gateway \
             agent:services/agent frontend:apps/frontend; do
  image="${REGISTRY}/urumi-${entry%%:*}:${TAG}"
  echo "building and pushing ${image}"
  docker buildx build --platform "${PLATFORM}" -t "${image}" --push "${ROOT}/${entry#*:}"
done
echo "pushed tag ${TAG}"
