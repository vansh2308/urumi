#!/usr/bin/env bash
# Create or update the Kubernetes Secrets the Urumi services read, from each service's local
# .env file (gitignored). Values never appear on the command line or in the output; they pass
# through a private temp file that is removed on exit.
#
#   deploy/scripts/create-local-secrets.sh [namespace]      # default: urumi
#
# Secrets (keys):
#   woocommerce-mcp-secrets  WC_CONSUMER_KEY, WC_CONSUMER_SECRET, DB_URL
#   gateway-secrets          DB_URL, AUTH_TOKEN_PEPPER, UPSTREAM_CREDENTIAL_KEYS
#   agent-secrets            DB_URL, GEMINI_API_KEY
#
# The gateway pepper must match the one used to create existing sessions and API keys, and the
# credential key the one that encrypted stored connector credentials, so both are copied from
# services/gateway/.env rather than generated. Create a credential key once with
#   (cd services/gateway && poetry run gateway-admin generate-credential-key)
# and put it in that .env as UPSTREAM_CREDENTIAL_KEYS=...
set -euo pipefail

NAMESPACE="${1:-urumi}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORKDIR="$(mktemp -d)"
chmod 700 "${WORKDIR}"
trap 'rm -rf "${WORKDIR}"' EXIT

# Print KEY=VALUE for each requested key found in an env file: surrounding quotes and a trailing
# " # comment" are removed, as python-dotenv does for unquoted values.
extract() {
  local file="$1"; shift
  local key line value
  for key in "$@"; do
    line="$(grep -E "^[[:space:]]*${key}=" "${file}" | tail -n 1 || true)"
    if [[ -z "${line}" ]]; then
      echo "missing ${key} in ${file}" >&2
      return 1
    fi
    value="${line#*=}"
    value="${value%%[[:space:]]#*}"
    value="${value%"${value##*[![:space:]]}"}"
    value="${value#\"}"; value="${value%\"}"
    value="${value#\'}"; value="${value%\'}"
    printf '%s=%s\n' "${key}" "${value}"
  done
}

apply_secret() {
  local name="$1" env_file="$2"; shift 2
  local out="${WORKDIR}/${name}.env"
  extract "${env_file}" "$@" > "${out}"
  kubectl -n "${NAMESPACE}" create secret generic "${name}" --from-env-file="${out}" \
    --dry-run=client -o yaml \
    | kubectl label --local -f - -o yaml app.kubernetes.io/part-of=urumi \
    | kubectl apply -f - >/dev/null
  echo "secret/${name}: $* (namespace ${NAMESPACE})"
}

kubectl get namespace "${NAMESPACE}" >/dev/null
apply_secret woocommerce-mcp-secrets "${ROOT}/services/woocommerce-mcp/.env" \
  WC_CONSUMER_KEY WC_CONSUMER_SECRET DB_URL
apply_secret gateway-secrets "${ROOT}/services/gateway/.env" \
  DB_URL AUTH_TOKEN_PEPPER UPSTREAM_CREDENTIAL_KEYS
apply_secret agent-secrets "${ROOT}/services/agent/.env" DB_URL GEMINI_API_KEY
