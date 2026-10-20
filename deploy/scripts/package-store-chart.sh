#!/usr/bin/env bash
# Put the urumi-service chart into deploy/helm/urumi-store/charts/ next to the vendored Bitnami
# WordPress chart, so the store chart installs without `helm dependency build` (no downloads).
#
#   deploy/scripts/package-store-chart.sh [output-dir]   # optionally also `helm package` the store chart
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHART="${ROOT}/deploy/helm/urumi-store"
rm -f "${CHART}"/charts/urumi-service-*.tgz
helm package "${ROOT}/deploy/helm/urumi-service" -d "${CHART}/charts" >/dev/null
if [[ $# -gt 0 ]]; then
  mkdir -p "$1"
  helm package "${CHART}" -d "$1" >/dev/null
fi
