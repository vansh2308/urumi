#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu/Debian VPS as a single-node k3s cluster for Urumi.
# Run on the server as root (or with sudo). Versions are pinned; bump them deliberately.
#
#   curl -fsSL https://raw.githubusercontent.com/<you>/<repo>/main/deploy/scripts/vps-bootstrap.sh | sudo bash
#   # or copy it over and: sudo bash vps-bootstrap.sh
#
# Installs: k3s (with its Traefik ingress on 80/443, local-path storage and the NetworkPolicy
# controller), Helm, and cert-manager. It does not create Secrets or deploy anything.
set -euo pipefail

K3S_VERSION="${K3S_VERSION:-v1.35.5+k3s1}"
CERT_MANAGER_VERSION="${CERT_MANAGER_VERSION:-v1.19.1}"
HELM_VERSION="${HELM_VERSION:-v3.19.0}"

if ! command -v k3s >/dev/null; then
  curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="${K3S_VERSION}" sh -s - \
    --write-kubeconfig-mode 0600
fi
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl wait --for=condition=Ready node --all --timeout=180s

if ! command -v helm >/dev/null; then
  curl -fsSL "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" | tar -xz -C /tmp
  install -m 0755 /tmp/linux-amd64/helm /usr/local/bin/helm
fi

helm repo add jetstack https://charts.jetstack.io >/dev/null
helm repo update jetstack >/dev/null
helm upgrade --install cert-manager jetstack/cert-manager -n cert-manager --create-namespace \
  --version "${CERT_MANAGER_VERSION}" --set crds.enabled=true --wait

kubectl create namespace urumi --dry-run=client -o yaml | kubectl apply -f -
kubectl label namespace urumi --overwrite \
  pod-security.kubernetes.io/enforce=restricted pod-security.kubernetes.io/warn=restricted

cat <<MSG
k3s is ready. Next (see docs/vps.md):
  1. kubectl apply -f deploy/vps/cluster-issuer.yaml   (after setting your email)
  2. copy /etc/rancher/k3s/k3s.yaml to your machine (replace 127.0.0.1 with the server IP)
  3. create the Secrets, install the store, run deploy/scripts/deploy-vps.sh
Kubernetes API endpoint for the gateway's egress rule: $(kubectl get endpoints kubernetes -o jsonpath='{.subsets[0].addresses[0].ip}')
MSG
