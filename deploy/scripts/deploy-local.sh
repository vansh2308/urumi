#!/usr/bin/env bash
# Build the four images, import them into the k3d cluster and install/upgrade the `urumi` release.
#
#   deploy/scripts/deploy-local.sh            # all services, tag = current commit
#   SKIP_BUILD=1 deploy/scripts/deploy-local.sh
#
# Requires the Secrets from create-local-secrets.sh. Touches only the `urumi` release: the
# `store` release (WordPress, MariaDB and their volumes) is never modified.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CLUSTER="${CLUSTER:-urumi-local}"
NAMESPACE="${NAMESPACE:-urumi}"
TAG="${TAG:-$(git -C "${ROOT}" rev-parse --short HEAD)}"
if [[ -n "$(git -C "${ROOT}" status --porcelain -- services apps)" ]]; then
  TAG="${TAG}-dirty"  # never reuse a commit tag for uncommitted code
fi

# image name:build context (bash 3 compatible, as shipped with macOS)
COMPONENTS="woocommerce-mcp:services/woocommerce-mcp gateway:services/gateway agent:services/agent frontend:apps/frontend"

images=()
for entry in ${COMPONENTS}; do
  name="${entry%%:*}"
  images+=("urumi/${name}:${TAG}")
  if [[ -z "${SKIP_BUILD:-}" ]]; then
    echo "building urumi/${name}:${TAG}"
    docker build -q -t "urumi/${name}:${TAG}" "${ROOT}/${entry#*:}" >/dev/null
  fi
done
k3d image import -c "${CLUSTER}" "${images[@]}"

helm dependency build "${ROOT}/deploy/helm/urumi" >/dev/null
helm upgrade --install urumi "${ROOT}/deploy/helm/urumi" -n "${NAMESPACE}" \
  -f "${ROOT}/deploy/helm/urumi/values.yaml" \
  -f "${ROOT}/deploy/helm/urumi/values-local.yaml" \
  --set global.imageTag="${TAG}" \
  --wait --timeout 10m
kubectl -n "${NAMESPACE}" get pods -l app.kubernetes.io/instance=urumi
