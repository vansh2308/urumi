#!/usr/bin/env bash
# Build the images for the VPS architecture and push them to a registry: the four services and
# urumi-store-wordpress (the image of every provisioned store). The gateway image carries the
# urumi-store chart for the provisioner.
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
if [[ -n "$(git -C "${ROOT}" status --porcelain -- services apps deploy/images deploy/helm/urumi-store)" ]]; then
  echo "uncommitted changes in services/ or apps/: commit before pushing ${TAG}" >&2
  exit 1
fi

"${ROOT}/deploy/scripts/package-store-chart.sh"
for entry in woocommerce-mcp:services/woocommerce-mcp gateway:services/gateway \
             agent:services/agent frontend:apps/frontend \
             store-wordpress:deploy/images/store-wordpress; do
  name="${entry%%:*}"
  image="${REGISTRY}/urumi-${name}:${TAG}"
  extra=()
  if [[ "${name}" == "gateway" ]]; then
    extra=(--build-context "store-chart=${ROOT}/deploy/helm/urumi-store")
  fi
  echo "building and pushing ${image}"
  docker buildx build --platform "${PLATFORM}" "${extra[@]}" -t "${image}" --push "${ROOT}/${entry#*:}"
done
echo "pushed tag ${TAG}"
