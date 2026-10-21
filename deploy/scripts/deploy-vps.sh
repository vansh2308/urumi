#!/usr/bin/env bash
# Install or upgrade the `urumi` release on the VPS cluster (KUBECONFIG must point at it).
#
#   DOMAIN=example.com REGISTRY=ghcr.io/your-org TAG=<sha> deploy/scripts/deploy-vps.sh
#
# Hosts: app.<DOMAIN> (dashboard), gateway.<DOMAIN> (API + MCP), shop.<DOMAIN> (store).
# EXTRA_VALUES may name one more values file (e.g. with the node IP for the API egress rule).
# --atomic rolls the release back automatically if the upgrade fails.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DOMAIN="${DOMAIN:?set DOMAIN, e.g. example.com}"
REGISTRY="${REGISTRY:?set REGISTRY, e.g. ghcr.io/your-org}"
TAG="${TAG:-$(git -C "${ROOT}" rev-parse --short HEAD)}"
NAMESPACE="${NAMESPACE:-urumi}"
CHART="${ROOT}/deploy/helm/urumi"

extra=()
[[ -n "${EXTRA_VALUES:-}" ]] && extra+=(-f "${EXTRA_VALUES}")

helm dependency update "${CHART}" >/dev/null  # file:// charts only; no download
helm upgrade --install urumi "${CHART}" -n "${NAMESPACE}" \
  -f "${CHART}/values.yaml" -f "${CHART}/values-production.yaml" ${extra[@]+"${extra[@]}"} \
  --set global.imageTag="${TAG}" \
  --set woocommerce-mcp.image.repository="${REGISTRY}/urumi-woocommerce-mcp" \
  --set gateway.image.repository="${REGISTRY}/urumi-gateway" \
  --set agent.image.repository="${REGISTRY}/urumi-agent" \
  --set frontend.image.repository="${REGISTRY}/urumi-frontend" \
  --set provisioner.image.repository="${REGISTRY}/urumi-gateway" \
  --set provisioner.env.PROVISIONER_STORE_IMAGE="${REGISTRY}/urumi-store-wordpress:${TAG}" \
  --set provisioner.env.PROVISIONER_MCP_IMAGE="${REGISTRY}/urumi-woocommerce-mcp:${TAG}" \
  --set gateway.env.STORES_BASE_DOMAIN="shop.${DOMAIN}" \
  --set provisioner.env.STORES_BASE_DOMAIN="shop.${DOMAIN}" \
  --set woocommerce-mcp.env.WC_BASE_URL="https://shop.${DOMAIN}" \
  --set gateway.env.OAUTH_ISSUER_URL="https://gateway.${DOMAIN}" \
  --set gateway.env.OAUTH_CONSENT_URL="https://app.${DOMAIN}/oauth/consent" \
  --set "woocommerce-mcp.hostAliasesFromServices[0].hostnames[0]=shop.${DOMAIN}" \
  --set woocommerce-mcp.hostAliasesFromServices[0].service=traefik \
  --set woocommerce-mcp.hostAliasesFromServices[0].namespace=kube-system \
  --set "gateway.ingress.hosts[0].host=gateway.${DOMAIN}" \
  --set "gateway.ingress.hosts[0].paths[0].path=/" \
  --set "gateway.ingress.tls[0].secretName=gateway-tls" \
  --set "gateway.ingress.tls[0].hosts[0]=gateway.${DOMAIN}" \
  --set "frontend.ingress.hosts[0].host=app.${DOMAIN}" \
  --set "frontend.ingress.hosts[0].paths[0].path=/" \
  --set "frontend.ingress.tls[0].secretName=app-tls" \
  --set "frontend.ingress.tls[0].hosts[0]=app.${DOMAIN}" \
  --atomic --wait --timeout 10m
kubectl -n "${NAMESPACE}" get pods,ingress -l app.kubernetes.io/instance=urumi
helm -n "${NAMESPACE}" history urumi --max 5
