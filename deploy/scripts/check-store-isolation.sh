#!/usr/bin/env bash
# Prove that two provisioned stores cannot reach each other (docs/stores.md, "Isolation").
#
#   deploy/scripts/check-store-isolation.sh acme globex
#
# Every line is a TCP connect attempt from inside a pod, expected to be ALLOWED or BLOCKED by
# the NetworkPolicies. Exits non-zero if any result differs from the expectation.
set -euo pipefail

A="${1:?usage: $0 <store-slug-a> <store-slug-b>}"
B="${2:?usage: $0 <store-slug-a> <store-slug-b>}"
PLATFORM_NS="${PLATFORM_NS:-urumi}"
failures=0

# connect <namespace> <workload> <container> <host> <port> <ALLOWED|BLOCKED> <description>
connect() {
  local ns="$1" workload="$2" container="$3" host="$4" port="$5" expected="$6" what="$7" result
  local probe="import socket,sys
s=socket.socket(); s.settimeout(4)
try: s.connect((sys.argv[1], int(sys.argv[2]))); print('ALLOWED')
except Exception: print('BLOCKED')"
  if [[ "${container}" == "wordpress" ]]; then
    # The Bitnami image has bash but no Python.
    result="$(kubectl -n "${ns}" exec "${workload}" -c "${container}" -- \
      bash -c "timeout 4 bash -c '</dev/tcp/${host}/${port}' 2>/dev/null && echo ALLOWED || echo BLOCKED")"
  else
    result="$(kubectl -n "${ns}" exec "${workload}" -c "${container}" -- python -c "${probe}" "${host}" "${port}")"
  fi
  if [[ "${result}" == "${expected}" ]]; then
    printf 'ok    %-8s %s\n' "${result}" "${what}"
  else
    printf 'FAIL  %-8s %s (expected %s)\n' "${result}" "${what}" "${expected}"
    failures=$((failures + 1))
  fi
}

svc() { echo "$1.store-$2.svc.cluster.local"; }

echo "== from store ${A}'s MCP server"
connect "store-${A}" deploy/store-mcp store-mcp "$(svc store-wordpress "${A}")" 80 ALLOWED "its own WordPress"
connect "store-${A}" deploy/store-mcp store-mcp "$(svc store-wordpress "${B}")" 80 BLOCKED "store ${B}'s WordPress"
connect "store-${A}" deploy/store-mcp store-mcp "$(svc store-mariadb "${B}")" 3306 BLOCKED "store ${B}'s MariaDB"
connect "store-${A}" deploy/store-mcp store-mcp "$(svc store-mcp "${B}")" 8000 BLOCKED "store ${B}'s MCP server"
connect "store-${A}" deploy/store-mcp store-mcp "gateway.${PLATFORM_NS}.svc.cluster.local" 8100 BLOCKED "the platform gateway"
connect "store-${A}" deploy/store-mcp store-mcp "$(svc store-mariadb "${A}")" 3306 BLOCKED "its own MariaDB (only WordPress may)"

echo "== from store ${A}'s WordPress"
connect "store-${A}" deploy/store-wordpress wordpress "$(svc store-mariadb "${A}")" 3306 ALLOWED "its own MariaDB"
connect "store-${A}" deploy/store-wordpress wordpress "$(svc store-mariadb "${B}")" 3306 BLOCKED "store ${B}'s MariaDB"
connect "store-${A}" deploy/store-wordpress wordpress "$(svc store-wordpress "${B}")" 80 BLOCKED "store ${B}'s WordPress"
connect "store-${A}" deploy/store-wordpress wordpress "1.1.1.1" 443 BLOCKED "the internet"

echo "== from the platform"
connect "${PLATFORM_NS}" deploy/gateway gateway "$(svc store-mcp "${A}")" 8000 ALLOWED "gateway -> store ${A}'s MCP server"
connect "${PLATFORM_NS}" deploy/agent agent "$(svc store-mcp "${A}")" 8000 BLOCKED "agent -> store ${A}'s MCP server (only the gateway may)"
connect "${PLATFORM_NS}" deploy/woocommerce-mcp woocommerce-mcp "$(svc store-wordpress "${A}")" 80 BLOCKED "core MCP server -> store ${A}'s WordPress"

if ((failures)); then
  echo "${failures} check(s) failed" >&2
  exit 1
fi
echo "all isolation checks passed"
