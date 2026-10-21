#!/usr/bin/env bash
# Build the images, import them into the k3d cluster and install/upgrade the `urumi` release.
# The gateway image also carries the store provisioner, with the urumi-store chart baked in;
# urumi/store-wordpress is the image every provisioned store runs.
#
#   deploy/scripts/deploy-local.sh            # all services, tag = current commit
#   SKIP_BUILD=1 deploy/scripts/deploy-local.sh
#
# Requires the Secrets from create-local-secrets.sh. `--atomic` rolls the release back to the
# previous revision automatically if the upgrade (or a migration) fails. Touches only the `urumi` release: the
# `store` release (WordPress, MariaDB and their volumes) is never modified.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CLUSTER="${CLUSTER:-urumi-local}"
NAMESPACE="${NAMESPACE:-urumi}"
TAG="${TAG:-$(git -C "${ROOT}" rev-parse --short HEAD)}"
if [[ -n "$(git -C "${ROOT}" status --porcelain -- services apps)" ]]; then
  TAG="${TAG}-dirty"  # never reuse a commit tag for uncommitted code
fi

if [[ -n "$(git -C "${ROOT}" status --porcelain -- deploy/images deploy/helm/urumi-store)" ]]; then
  TAG="${TAG%-dirty}-dirty"
fi

# image name:build context (bash 3 compatible, as shipped with macOS)
COMPONENTS="woocommerce-mcp:services/woocommerce-mcp gateway:services/gateway agent:services/agent frontend:apps/frontend store-wordpress:deploy/images/store-wordpress"

# The store chart's dependencies (vendored Bitnami chart + urumi-service) for the gateway image.
"${ROOT}/deploy/scripts/package-store-chart.sh"

images=()
for entry in ${COMPONENTS}; do
  name="${entry%%:*}"
  images+=("urumi/${name}:${TAG}")
  if [[ -z "${SKIP_BUILD:-}" ]]; then
    echo "building urumi/${name}:${TAG}"
    extra=()
    if [[ "${name}" == "gateway" ]]; then
      extra=(--build-context "store-chart=${ROOT}/deploy/helm/urumi-store")
    fi
    docker build -q ${extra[@]+"${extra[@]}"} -t "urumi/${name}:${TAG}" "${ROOT}/${entry#*:}" >/dev/null
  fi
done
k3d image import -c "${CLUSTER}" "${images[@]}"

# Every pod in the namespace (ours and the store) meets the "restricted" Pod Security Standard;
# enforce it so a weaker pod is rejected. The label is idempotent and the namespace is not owned
# by Helm (the store release shares it).
kubectl label namespace "${NAMESPACE}" --overwrite \
  pod-security.kubernetes.io/enforce=restricted \
  pod-security.kubernetes.io/enforce-version=latest \
  pod-security.kubernetes.io/warn=restricted >/dev/null

helm dependency update "${ROOT}/deploy/helm/urumi" >/dev/null  # file:// charts only; no download
helm upgrade --install urumi "${ROOT}/deploy/helm/urumi" -n "${NAMESPACE}" \
  -f "${ROOT}/deploy/helm/urumi/values.yaml" \
  -f "${ROOT}/deploy/helm/urumi/values-local.yaml" \
  --set global.imageTag="${TAG}" \
  --set provisioner.env.PROVISIONER_STORE_IMAGE="docker.io/urumi/store-wordpress:${TAG}" \
  --set provisioner.env.PROVISIONER_MCP_IMAGE="urumi/woocommerce-mcp:${TAG}" \
  --atomic --wait --timeout 10m
kubectl -n "${NAMESPACE}" get pods -l app.kubernetes.io/instance=urumi
