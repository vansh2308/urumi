# Deployment

How the Urumi services are tested, packaged and deployed: locally on the k3d cluster
(`urumi-local`) and on a VPS. The WooCommerce store (Bitnami WordPress + MariaDB, Helm release
`store`) is set up as described in [local-cluster.md](local-cluster.md) and is not managed by
anything below.

## What runs where

| Component | Image | Port | In-cluster DNS | Exposed (local) |
| --- | --- | --- | --- | --- |
| WooCommerce MCP server | `urumi/woocommerce-mcp` | 8000 | `woocommerce-mcp.urumi.svc.cluster.local` | never: only the gateway may connect |
| Gateway | `urumi/gateway` | 8100 | `gateway.urumi.svc.cluster.local` | `http://gateway.urumi.local` |
| Agent | `urumi/agent` | 8201 | `agent.urumi.svc.cluster.local` | `http://agent.urumi.local` |
| Dashboard (nginx) | `urumi/frontend` | 8080 | `frontend.urumi.svc.cluster.local` | `http://urumi.local` |
| Store (release `store`) | Bitnami WordPress | 80 | `store-wordpress.urumi.svc.cluster.local` | `http://wordpress.local` |
| Hosted MCP servers | admin-chosen (e.g. `mcp/duckduckgo`, `ghcr.io/github/github-mcp-server`) | per server | `mcp-<slug>.urumi-mcp.svc.cluster.local` | never: only the gateway may connect |

Hosted MCP servers are deployed and removed by the gateway itself, in their own `urumi-mcp`
namespace; see [hosted-mcp.md](hosted-mcp.md) for the design, the security controls and how the
GitHub and web search servers are connected.

```
browser ──► Traefik ──► frontend (nginx) ──/api/gateway──► gateway ──► woocommerce-mcp ──► store-wordpress
                    │                     └─/api/agent────► agent ──► gateway (user's API key)
                    ├──► gateway   (gateway.urumi.local: Postman, MCP clients)
                    └──► agent     (agent.urumi.local: Postman)
```

All four services are stateless. Their databases are the existing PostgreSQL databases on Neon
(`urumi`, `urumi_gateway`, `urumi_agent`), reached through `DB_URL` from a Secret; no database
runs in the cluster for them, and no Redis or broker is used. WordPress and MariaDB keep their
data on the `store-wordpress` and `data-store-mariadb-0` PersistentVolumeClaims.

## 1. Tests

Each service has its own suite; none of them touches the real store, the Neon databases or the
model provider. The Python suites create a throwaway database per run on a disposable local
PostgreSQL and drop it afterwards; without `TEST_DATABASE_URL` the database tests are skipped,
and a non-local host is refused.

```bash
docker run -d --rm --name urumi-test-postgres -e POSTGRES_PASSWORD=postgres \
  -p 127.0.0.1:55432:5432 --tmpfs /var/lib/postgresql/data postgres:16-alpine
export TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55432/postgres

(cd services/woocommerce-mcp && poetry run pytest)   # 33 tests
(cd services/gateway && poetry run pytest)           # 47 tests
(cd services/agent && poetry run pytest)             # 14 tests
(cd apps/frontend && npm test)                       # 10 tests

docker stop urumi-test-postgres
```

## 2. Images

Every image is built from its own directory, so each service can be built and released
independently.

| Image | Base | User | Notes |
| --- | --- | --- | --- |
| woocommerce-mcp, gateway, agent | `python:3.14-slim-bookworm`, multi-stage | uid 10001 | `poetry.lock`-pinned venv; `alembic` included for migrations; `gateway-admin` CLI in the gateway image |
| frontend | `node:22-alpine` build, `nginxinc/nginx-unprivileged:1.28-alpine` runtime | uid 101 | static bundle with relative `/api/...` URLs; nginx proxies them at run time |

The images contain no secrets and no environment-specific endpoints: every setting is an
environment variable. The entrypoints use exec form, so uvicorn (or nginx) is PID 1 and drains
in-flight requests on `SIGTERM`. Each image has a liveness `HEALTHCHECK`.

Build and run one locally:

```bash
TAG=$(git rev-parse --short HEAD)
docker build -t urumi/gateway:$TAG services/gateway
docker run --rm -p 127.0.0.1:8100:8100 \
  -e DB_URL='postgresql://…' -e AUTH_TOKEN_PEPPER='…' \
  -e UPSTREAM_ALLOWED_HOSTS='host.docker.internal:8000' \
  urumi/gateway:$TAG
curl http://127.0.0.1:8100/health/ready
```

The dashboard needs to know where the APIs are: `-e GATEWAY_UPSTREAM=http://<gateway>:8100
-e AGENT_UPSTREAM=http://<agent>:8201`.

Migrations run from the same image:

```bash
docker run --rm -e DB_URL='postgresql://…' --entrypoint alembic urumi/gateway:$TAG upgrade head
```

## 3. Deploy to the local k3d cluster

### One-time setup

1. The cluster and store from [local-cluster.md](local-cluster.md) are running
   (`kubectl config current-context` is `k3d-urumi-local`).
2. Add the platform host names (the ingress is published on host port 80):

   ```bash
   echo "127.0.0.1 urumi.local gateway.urumi.local agent.urumi.local" | sudo tee -a /etc/hosts
   ```

3. Each service's `.env` exists (they hold the real credentials and are gitignored). Create the
   Kubernetes Secrets from them:

   ```bash
   deploy/scripts/create-local-secrets.sh
   ```

   This writes only these keys, never printing their values:

   | Secret | Keys |
   | --- | --- |
   | `woocommerce-mcp-secrets` | `WC_CONSUMER_KEY`, `WC_CONSUMER_SECRET`, `DB_URL` |
   | `gateway-secrets` | `DB_URL`, `AUTH_TOKEN_PEPPER`, `UPSTREAM_CREDENTIAL_KEYS` |
   | `agent-secrets` | `DB_URL`, `GEMINI_API_KEY` |

   The gateway pepper is copied, not generated, because existing sessions and API keys are
   hashed with it. `UPSTREAM_CREDENTIAL_KEYS` (encrypts connector credentials) is copied for the
   same reason: the host and cluster gateways share one database. Create it once with
   `(cd services/gateway && poetry run gateway-admin generate-credential-key)` and add it to
   `services/gateway/.env`. Re-run the script after changing a `.env`, then restart the affected
   deployment (`kubectl -n urumi rollout restart deploy/gateway`).

### Deploy or upgrade

```bash
deploy/scripts/deploy-local.sh
```

It builds the four images tagged with the current commit (`<sha>-dirty` when `services/` or
`apps/` has uncommitted changes), imports them into the cluster with `k3d image import`, and
runs the equivalent of:

```bash
helm dependency build deploy/helm/urumi
helm upgrade --install urumi deploy/helm/urumi -n urumi \
  -f deploy/helm/urumi/values.yaml -f deploy/helm/urumi/values-local.yaml \
  --set global.imageTag=<sha> --wait --timeout 10m
```

Before new pods start, Helm runs one migration Job per service (`alembic upgrade head`, only
`DB_URL` mounted). If a migration fails, the release fails and the old pods keep serving.
`SKIP_BUILD=1` reuses images that are already built.

### Point the gateway at the in-cluster MCP server

The connector registry lives in the gateway's database, which the cluster shares with the
gateway you run on the host. A connector registered for host development points at
`http://127.0.0.1:8000/mcp`, which means nothing inside a pod. Calls fail with
`upstream_unavailable` until the connector uses the in-cluster address (the only address
`UPSTREAM_ALLOWED_HOSTS` accepts there):

```bash
G=http://gateway.urumi.local
TOKEN=$(curl -s $G/v1/auth/login -H 'content-type: application/json' \
  -d '{"email":"<platform admin email>","password":"<password>"}' | jq -r .access_token)
curl -s $G/v1/connectors -H "authorization: Bearer $TOKEN" | jq '.[] | {id, slug, url}'
curl -s -X PATCH $G/v1/connectors/<id> -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"url":"http://woocommerce-mcp.urumi.svc.cluster.local:8000/mcp"}'
curl -s -X POST $G/v1/connectors/<id>/probe -H "authorization: Bearer $TOKEN" | jq .tools[].name
```

The dashboard does the same from MCP servers → the connector → Edit. To return to host
development, set the URL back to `http://127.0.0.1:8000/mcp` (allowed by the host gateway's
`.env`). Alternatively register a second connector (for example `woocommerce-k8s`) so both
setups work side by side; team policies and grants are per connector.

### Verify

```bash
kubectl -n urumi get pods,ingress -l app.kubernetes.io/part-of=urumi
curl -4 -I http://wordpress.local/                         # 200
curl -4 -I http://wordpress.local/wp-admin/                # 302 to wp-login.php
curl http://urumi.local/api/gateway/health/ready           # dashboard -> gateway
curl http://urumi.local/api/agent/health/ready             # dashboard -> agent
curl http://gateway.urumi.local/health/ready
curl http://agent.urumi.local/health/ready

# Gateway pod -> MCP server -> WooCommerce REST API (DNS, NetworkPolicy, OAuth signing):
kubectl -n urumi exec deploy/gateway -- python -c '
import json, urllib.request
req = urllib.request.Request("http://woocommerce-mcp.urumi.svc.cluster.local:8000/mcp",
    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "list_products", "arguments": {"per_page": 2}}}).encode(),
    {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
print(json.load(urllib.request.urlopen(req, timeout=20))["result"]["structuredContent"]["total"])'

# Any other pod -> MCP server is refused:
kubectl -n urumi exec deploy/frontend -- wget -q -T 4 -O- \
  http://woocommerce-mcp.urumi.svc.cluster.local:8000/healthz || echo blocked
```

Then open `http://urumi.local`, sign in, and use the chat or the tool runner; with the connector
pointed at the cluster, a tool call goes browser → nginx → gateway → MCP server → WordPress.
Postman works unchanged with `http://gateway.urumi.local` and `http://agent.urumi.local` as the
base URLs.

## 4. Configuration, secrets and networking

**Configuration** (non-secret) is in `deploy/helm/urumi/values*.yaml` and becomes a ConfigMap per
service, loaded with `envFrom`; changing it rolls the pods (checksum annotation). **Secrets** are
referenced by name and key only (`secretEnv`); the chart never creates or contains them. In
production create them from a secret manager (External Secrets, Sealed Secrets, SOPS) instead of
the local script.

**Service-to-service traffic** always uses cluster DNS, never `localhost`:

| From | To | Setting |
| --- | --- | --- |
| dashboard nginx | gateway, agent | `GATEWAY_UPSTREAM`, `AGENT_UPSTREAM` (fully qualified: nginx ignores search domains) |
| agent | gateway | `GATEWAY_URL=http://gateway.urumi.svc.cluster.local:8100` |
| gateway | MCP server | connector URL, which must match `UPSTREAM_ALLOWED_HOSTS` (SSRF guard) |
| MCP server | WordPress | `WC_BASE_URL=http://wordpress.local` |

The MCP server signs WooCommerce requests with OAuth 1.0a over the store's canonical URL, which
must equal WordPress's `home` (`http://wordpress.local`). That name only exists in the host's
`/etc/hosts`, so the chart pins it inside the MCP pod to the `store-wordpress` Service's
ClusterIP (`hostAliasesFromServices`, looked up at install time). The pod therefore talks to
WordPress directly over the cluster network with an unchanged signature. On a VPS the store has
a real domain with HTTPS, the alias is not used, and authentication switches to HTTP Basic over
TLS automatically.

The MCP server's `MCP_ALLOWED_HOSTS` lists the Service names, so its DNS-rebinding protection
accepts the gateway's `Host` header. The MCP transport is stateless Streamable HTTP with JSON
responses, so any replica can serve any request without sticky sessions.

**NetworkPolicies** (enforced by k3s's built-in controller) allow only:

| Pod | Accepts traffic from |
| --- | --- |
| woocommerce-mcp | gateway pods |
| hosted MCP servers (`urumi-mcp`) | gateway pods; everything else is denied by a namespace default-deny policy, and their egress is limited to DNS and public addresses |
| gateway | Traefik, dashboard, agent |
| agent | Traefik, dashboard |
| frontend | Traefik |

Egress is denied by default too. Each service may reach DNS, the public internet on 443 and 5432
only (Neon, Gemini, HTTPS upstreams; private, CGNAT and metadata ranges excluded) and its exact
in-cluster peers: dashboard → gateway and agent, agent → gateway, gateway → WooCommerce MCP and
hosted servers, MCP server → store. The gateway also reaches the Kubernetes API through
`gateway.networkPolicy.extraEgress` (per environment: the k3d node network locally, the node's IP
on a VPS). Add rules with `networkPolicy.extraEgress` rather than replacing `egress`.

**Pod Security**: `deploy-local.sh` labels `urumi` to enforce the `restricted` Pod Security
Standard (every pod, the store's included, already meets it); `urumi-mcp` enforces it from the
chart. See [threat-model.md](threat-model.md) for the full threat model.

**ServiceAccounts**: one per service. Only the gateway mounts its token, and its only RBAC is a
Role in `urumi-mcp` (manage Deployments, Services, Secrets, NetworkPolicies; read Pods) for
hosted MCP servers. It has no rights in `urumi` or cluster-wide. The other services mount no
token and have no bindings.

**Ingress**: Traefik (`className: traefik`). The agent streams answers as server-sent events;
Traefik flushes them as they arrive and the dashboard's nginx has buffering disabled for
`/api/agent`. The gateway and agent trust `X-Forwarded-For` only from the pod network
(`10.42.0.0/16`), so per-IP login throttling cannot be bypassed with a forged header.

**Resources and probes**: every container has requests and a memory limit (no CPU limit, to
avoid throttling). Startup probes cover slow starts, liveness checks only the process, and
readiness checks the database (the MCP server does not check the store, so a store outage does
not drain every replica).

## OAuth for MCP clients

The gateway is also an OAuth 2.1 authorization server for `/v1/mcp` (MCP authorization spec):
clients such as Claude Code connect with only the URL and the user approves them on the
dashboard. Set per environment through `OAUTH_ISSUER_URL` and `OAUTH_CONSENT_URL` (Helm values);
design, endpoints and client setup are in [oauth.md](oauth.md).

## Observability

| Question | Where to look |
| --- | --- |
| Is each server up, and if not, why? | Dashboard → MCP servers (Health column, hover for the reason) or `GET /v1/teams/{team}/connectors/status`: a live MCP ping per server with `connected` / `unreachable` (+ `error_code`, `error_message`) / `disabled`, latency, last success and last failure. Platform admins: `GET /v1/connectors/status` (every server). |
| Why did a call fail? | Dashboard → Audit logs and Usage (per call: user, server, tool, outcome, error code, duration, request id). Error codes: `upstream_unavailable`, `upstream_timeout`, `upstream_unauthorized`, `upstream_blocked`, `upstream_bad_response`, `upstream_rejected`, `upstream_busy`, `tool_error`, `denied`. |
| Calls, error rate, latency per server | Dashboard → Usage / Overview (from the database), or Prometheus. |
| Following one request | Every response has `X-Request-ID`; the agent, gateway and MCP servers log it on every line (JSON logs: `kubectl -n urumi logs deploy/<name>`). |

Prometheus metrics (gateway; the agent has `agent_*` equivalents for runs, LLM calls, tokens and
tool calls):

| Metric | Labels |
| --- | --- |
| `gateway_tool_invocations_total` | `connector`, `tool`, `outcome` |
| `gateway_tool_invocation_duration_seconds` | `connector` |
| `gateway_upstream_failures_total` | `connector`, `reason` |
| `gateway_upstream_up` | `connector` (1/0 from the last exchange) |
| `gateway_http_requests_total`, `gateway_http_request_duration_seconds` | `method`, `route`, `status` |
| `gateway_rate_limited_total`, `gateway_auth_failures_total` | `scope` / `kind` |

`/metrics` answers only direct in-cluster requests (anything through the ingress or the
dashboard proxy gets 404). The gateway and agent pods carry `prometheus.io/scrape|port|path`
annotations, so any Prometheus that discovers pods by annotation scrapes them; none is installed
by these charts, to keep the cluster light. To add one later:

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install monitoring prometheus-community/prometheus -n monitoring --create-namespace \
  --set alertmanager.enabled=false --set prometheus-pushgateway.enabled=false
```

(and allow ingress from the `monitoring` namespace in the gateway and agent NetworkPolicies).
Useful queries: error rate per server
`sum by (connector) (rate(gateway_tool_invocations_total{outcome!="success"}[5m])) / sum by (connector) (rate(gateway_tool_invocations_total[5m]))`,
p95 latency `histogram_quantile(0.95, sum by (le, connector) (rate(gateway_tool_invocation_duration_seconds_bucket[5m])))`.

## 5. Upgrades and rollbacks

Step-by-step procedures for the platform and the store, store backups and restores, and the
results of a rollback drill under load are in [upgrades.md](upgrades.md). In short:

* Each deploy is a Helm revision. Images are tagged with the commit (`global.imageTag`, or a
  component's `image.tag`; `image.digest` pins by digest), so a revision always maps to exact
  code.
* Rolling updates use `maxUnavailable: 0, maxSurge: 1`: a new pod must pass readiness before an
  old one stops. Old pods first wait `preStopSleepSeconds` (5 s) for endpoint removal to reach
  Traefik and nginx, then drain in-flight requests within `terminationGracePeriodSeconds`.
* Upgrades use `--atomic`: a failed rollout or migration returns to the previous revision.
* Migrations run first and are forward-only. Keep them backward compatible with the previous
  release (add, backfill, then remove in a later release), because the old pods run until the
  rollout finishes and a rollback does not undo a migration.
* Roll back the platform: `helm -n urumi history urumi`, then
  `helm -n urumi rollback urumi <revision> --wait`.
* Upgrade one service only: `helm upgrade urumi … --reuse-values --set gateway.image.tag=<sha>`.

## 6. Cleaning up safely

```bash
helm -n urumi uninstall urumi
```

This removes only the four Deployments, their Services, ConfigMaps, Ingresses,
NetworkPolicies and ServiceAccounts, plus the gateway's Role, the quota and the default-deny
policy in `urumi-mcp`. The `urumi-mcp` namespace itself is kept, with any hosted servers in it:
delete hosted servers from the dashboard (or `DELETE /v1/connectors/{id}`) before uninstalling,
which removes their Deployment, Service, Secret and NetworkPolicy. It does not touch the `store` release, the WordPress or
MariaDB volumes, or the three Secrets (delete those explicitly if wanted:
`kubectl -n urumi delete secret woocommerce-mcp-secrets gateway-secrets agent-secrets`).

Never run `kubectl delete namespace urumi` or `helm uninstall store`: the first deletes the
store's PersistentVolumeClaims with everything else in the namespace, and the second deletes the
WordPress PVC (the MariaDB PVC, created from a StatefulSet template, survives). To make the
WordPress volume survive an uninstall too, the store values example sets
`persistence.annotations: {helm.sh/resource-policy: keep}`; it takes effect after the next
`helm upgrade store …` with those values.

## 7. Production on a VPS

The full walkthrough is [vps.md](vps.md): `deploy/scripts/vps-bootstrap.sh` (k3s, Helm,
cert-manager), the Let's Encrypt `ClusterIssuer` in `deploy/vps/`, Secrets with
`ENV_SUFFIX=.production deploy/scripts/create-local-secrets.sh`, the store from
`deploy/helm/woocommerce/values-production.example.yaml`, images with
`deploy/scripts/push-images.sh` (or `.github/workflows/images.yml`), and the release with
`deploy/scripts/deploy-vps.sh`. Only values change between environments; vps.md has the
local-vs-production table.

Production values run two replicas of each service with a PodDisruptionBudget, `APP_ENV=production`
(which makes the MCP server refuse a plain-HTTP store) and API docs disabled. Keep the MCP server
without an ingress: it has no authentication of its own and must stay reachable only from the
gateway.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Pod not Ready | `kubectl -n urumi describe pod …`; `kubectl -n urumi logs deploy/<name>` (JSON logs). Readiness fails when the database is unreachable or not migrated. |
| Release failed at a migration | `kubectl -n urumi logs job/<name>-migrate` (a failed Job is kept for an hour). |
| Tool calls return `upstream_unavailable` | The connector URL still points at `127.0.0.1`; see "Point the gateway at the in-cluster MCP server". |
| MCP tool calls fail to reach the store | `kubectl -n urumi get pod -l app.kubernetes.io/name=woocommerce-mcp -o jsonpath='{.items[0].spec.hostAliases}'` should map `wordpress.local` to the `store-wordpress` ClusterIP; redeploy if the store Service was recreated. |
| `421 Misdirected Request` from the MCP server | The `Host` the client sends is not in `MCP_ALLOWED_HOSTS`. |
| Hosted server not ready, or `upstream_unauthorized` | See the troubleshooting table in [hosted-mcp.md](hosted-mcp.md). |
| Dashboard shows "service unreachable" | `curl http://urumi.local/api/gateway/health/live`; check `GATEWAY_UPSTREAM`/`AGENT_UPSTREAM` and the NetworkPolicies. |
| Images not found (`ErrImageNeverPull`/`ImagePullBackOff`) | Re-run `deploy-local.sh` (it imports the images into k3d). |
