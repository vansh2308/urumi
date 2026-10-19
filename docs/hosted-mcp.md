# Upstream credentials and hosted MCP servers

The gateway connects to three kinds of upstream MCP servers:

| Server | Kind | How it runs | Credential |
| --- | --- | --- | --- |
| `woocommerce` | URL | our own `woocommerce-mcp` Deployment (Helm) | none (network-restricted) |
| `web-search` | hosted | `mcp/duckduckgo` image, deployed by the gateway | none (keyless) |
| `github` | hosted | `ghcr.io/github/github-mcp-server` image, deployed by the gateway | GitHub token, bearer |

Any server can be registered **by URL** (`POST /v1/connectors`) or deployed **from a container
image** (`POST /v1/hosted-servers`). Both become ordinary connectors: teams enable them, members
get grants, and every call is authorized, rate limited and audited the same way.

## Upstream credentials

Admins can attach one static credential to a connector: a bearer token
(`Authorization: Bearer <token>`) or a custom header (`X-Api-Key: <value>`).

* The value is encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256) using
  `UPSTREAM_CREDENTIAL_KEYS`, which lives in the `gateway-secrets` Secret. The database holds
  only ciphertext and the header name.
* It is decrypted only to build a request, added only to requests whose origin is the
  connector's own URL (redirects are never followed), and never logged or returned. The API and
  dashboard show only the header name.
* Headers the transport owns (`Host`, `Content-Type`, `Mcp-Session-Id`, `X-Request-ID`, …) cannot
  be used as credential headers.
* Rotating the key: generate a new one (`gateway-admin generate-credential-key`), set
  `UPSTREAM_CREDENTIAL_KEYS=<new>,<old>`, re-save each credential, then drop the old key.
* An upstream that answers 401/403 is reported as `upstream_unauthorized`.

This was the cause of the original `401 Unauthorized` from GitHub: the GitHub MCP server needs
a token on every request, and the gateway had no way to send one.

## Hosted MCP servers

```
admin ──POST /v1/hosted-servers──► gateway ──(ServiceAccount, Role in urumi-mcp only)──► Kubernetes API
                                      │
                                      │  creates, in namespace urumi-mcp:
                                      │    Secret mcp-<slug>-env    (secret variables)
                                      │    Deployment mcp-<slug>    (1 pod, non-root, limits)
                                      │    Service mcp-<slug>       (ClusterIP)
                                      │    NetworkPolicy mcp-<slug> (in: gateway only; out: DNS + internet)
                                      │  and registers the connector
                                      ▼    http://mcp-<slug>.urumi-mcp.svc.cluster.local:<port><path>
gateway ──MCP Streamable HTTP (+ credential header)──► mcp-<slug> pod ──► public API (GitHub, DuckDuckGo)
```

### What the platform enforces

| Concern | Where | How |
| --- | --- | --- |
| Which images | gateway | `HOSTED_ALLOWED_IMAGES` (fnmatch patterns). Anything else is refused with 422. |
| Who | gateway | Platform admins only (session token, never an API key). |
| Kubernetes permissions | `templates/hosted-mcp.yaml` | The gateway's Role allows Deployments, Services, Secrets, NetworkPolicies (manage) and Pods (read) in `urumi-mcp` only. It cannot touch `urumi`, the store or anything cluster-wide. |
| Pod hardening | gateway manifests + namespace | `runAsNonRoot` (uid 65532), read-only root filesystem with a 64 Mi `/tmp`, all capabilities dropped, seccomp `RuntimeDefault`, no ServiceAccount token, no service links. The namespace enforces the `restricted` Pod Security Standard, so a weaker pod would be rejected anyway. |
| Limits | gateway + namespace | Per server CPU/memory limits (default 250m / 256 Mi, max 1 CPU / 1 Gi). ResourceQuota and LimitRange cap the namespace (10 pods, 4 CPU, 4 Gi). At most `HOSTED_MAX_SERVERS` servers. |
| Network | NetworkPolicies | Namespace default-deny. Each server admits only gateway pods on its port, and may only reach DNS and public addresses on 80/443; `10/8`, `172.16/12`, `192.168/16`, `100.64/10` and `169.254/16` (cloud metadata) are excluded. Egress can be set to `none` (DNS only). |
| Secrets | Kubernetes Secret | Secret variables are written only to `mcp-<slug>-env` and injected with `envFrom`. The gateway database stores their names, never their values. |
| Status | gateway | Read live from the Deployment and its pods: `deploying`, `ready`, `failed` (with the reason, e.g. `ImagePullBackOff`), `stopped`, `missing`. |

### Lifecycle

| Action | API | Cluster effect |
| --- | --- | --- |
| Deploy | `POST /v1/hosted-servers` | Server-side apply of the four objects. If any step fails, they are deleted and the registry row is removed, so a retry starts clean. |
| Update | `PATCH /v1/hosted-servers/{id}` | Image, args, variables, secret variables (set or remove), limits, egress. Rolling update (`maxUnavailable: 0`). |
| Restart | `POST /v1/hosted-servers/{id}/restart` | New pod with the same spec. |
| Disable / enable | `PATCH /v1/connectors/{id}` `{"enabled": false}` | Tools disappear for every team and the Deployment scales to 0; enabling scales back to 1. |
| Remove | `DELETE /v1/connectors/{id}` | Deletes the Deployment, Service, NetworkPolicy and Secret, then the connector, team policies and grants. Audit and usage history is kept. |

The `urumi-mcp` namespace is kept on `helm uninstall` (`helm.sh/resource-policy: keep`) so
running servers are never deleted behind the gateway's back. Remove servers through the gateway
first, then `kubectl delete namespace urumi-mcp` if you want it gone.

## Connecting the two extra servers

Sign in to the dashboard as a platform admin (`http://urumi.local`), then **MCP servers → Add
server → Deploy from container image**.

### Web search (DuckDuckGo, no key)

| Field | Value |
| --- | --- |
| Slug | `web-search` |
| Display name | `Web search (DuckDuckGo)` |
| Image | `mcp/duckduckgo:latest` |
| Port / path | `8000` / `/mcp` |
| Arguments (one per line) | `python`<br>`-c`<br>`import sys; from duckduckgo_mcp_server import server; server.mcp.settings.transport_security = None; sys.argv[1:] = ['--transport', 'streamable-http', '--host', '0.0.0.0', '--port', '8000']; server.main()` |
| Outbound network | Public internet |

Why the long argument: the published `mcp/duckduckgo` image (server 0.5.0) only accepts requests
whose `Host` is `localhost`, its built-in DNS-rebinding protection for local use, so the
gateway's `Host: mcp-web-search.urumi-mcp.svc.cluster.local` gets `421 Misdirected Request`. The
arguments start the same server with that check off. That is safe here because the
NetworkPolicy admits only the gateway. Newer releases of the server have `--allowed-hosts`
instead; switch to it when the image is updated.

Tools: `search`, `fetch_content`. The server refuses to fetch private or loopback addresses on
its own, and the NetworkPolicy blocks them as well.

### GitHub (official server, token)

1. Create a fine-grained personal access token on GitHub (Settings → Developer settings →
   Fine-grained tokens): pick the repositories, grant read-only *Contents*, *Issues*, *Pull
   requests* and *Metadata*. Read-only is enough for the demo.
2. Deploy:

   | Field | Value |
   | --- | --- |
   | Slug | `github` |
   | Image | `ghcr.io/github/github-mcp-server:latest` |
   | Port / path | `8082` / `/` |
   | Arguments | `http`<br>`--port`<br>`8082`<br>`--toolsets`<br>`context,repos,issues,pull_requests,users` |
   | Credential | Bearer token → paste the token |
   | Outbound network | Public internet |

   In HTTP mode the GitHub server takes the token from each request's `Authorization` header,
   so it is stored as the connector's credential (encrypted) rather than as a pod variable. Add
   `--read-only` to the arguments to hide every write tool.
3. Already deployed without a token? Open the server → **Edit** → Credential → *Bearer token*.

Then, as a team admin, enable both servers for the team (**MCP servers → ⋯ → Enable for team**)
and grant members the tools they need.

### Same steps with curl

```bash
G=http://gateway.urumi.local
TOKEN=$(curl -s $G/v1/auth/login -H 'content-type: application/json' \
  -d '{"email":"<platform admin>","password":"<password>"}' | jq -r .access_token)

curl -s $G/v1/hosted-servers -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{
  "slug": "github", "display_name": "GitHub", "image": "ghcr.io/github/github-mcp-server:latest",
  "port": 8082, "path": "/",
  "args": ["http", "--port", "8082", "--toolsets", "context,repos,issues,pull_requests,users"],
  "credential": {"type": "bearer", "value": "'"$GITHUB_TOKEN"'"}
}' | jq '{id: .connector.id, url: .connector.url, status}'

curl -s $G/v1/hosted-servers -H "authorization: Bearer $TOKEN" | jq '.[] | {slug: .connector.slug, status}'
curl -s -X POST $G/v1/connectors/<id>/probe -H "authorization: Bearer $TOKEN" | jq '.tools[].name'
```

## Verify isolation

```bash
kubectl -n urumi-mcp get deploy,pods,svc,networkpolicy,secret,resourcequota

# A hosted pod cannot reach the store, the WooCommerce MCP server, the API server or metadata:
kubectl -n urumi-mcp exec deploy/mcp-web-search -- python -c '
import socket
for h, p in [("store-wordpress.urumi.svc.cluster.local", 80), ("woocommerce-mcp.urumi.svc.cluster.local", 8000),
             ("kubernetes.default.svc", 443), ("169.254.169.254", 80), ("duckduckgo.com", 443)]:
    s = socket.socket(); s.settimeout(3)
    try: s.connect((h, p)); print(h, "open")
    except OSError as e: print(h, "blocked", type(e).__name__)'

# Only the gateway can reach a hosted server:
kubectl -n urumi exec deploy/agent -- python -c '
import socket; s = socket.socket(); s.settimeout(3)
s.connect(("mcp-web-search.urumi-mcp.svc.cluster.local", 8000))' || echo blocked

# The gateway can manage hosted servers and nothing else:
kubectl auth can-i create deployments -n urumi-mcp --as=system:serviceaccount:urumi:gateway  # yes
kubectl auth can-i create deployments -n urumi --as=system:serviceaccount:urumi:gateway      # no
kubectl auth can-i get secrets -n urumi --as=system:serviceaccount:urumi:gateway             # no
```

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| Deploy returns 422 "not on the gateway's allow-list" | Add the image pattern to `HOSTED_ALLOWED_IMAGES` in the Helm values. |
| Status `failed`, `ImagePullBackOff` | Image name or tag is wrong, or the registry is private (add an `imagePullSecret` to the namespace's default ServiceAccount). |
| Status `failed`, `CrashLoopBackOff` | `kubectl -n urumi-mcp logs deploy/mcp-<slug>`; often the image needs a writable path other than `/tmp`, or must run as root (not allowed). |
| Probe returns `upstream_bad_response` (HTTP 421) | The server's DNS-rebinding protection rejects the Service host name; configure its allowed hosts (see web search). |
| Probe returns `upstream_unauthorized` | The server wants a credential; set it under Edit → Credential. |
| Deploy returns 502 with `exceeded quota` | The namespace ResourceQuota is full; remove a server or raise `hostedMcp.quota`. |
| "Hosted MCP servers are not enabled on this gateway" | The gateway runs outside the cluster (host development) or `HOSTED_ENABLED=false`. |
