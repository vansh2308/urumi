# Bonus and stand-out items: status and plan

Status is checked against the code on `master` (2026-10-19) and the requirements PDF. Nothing below is implemented yet; this is the plan only.

| # | Item | Status | Effort |
| --- | --- | --- | --- |
| 1 | Single MCP endpoint for every enabled server | **Missing.** This is core, not bonus: the PDF's definition of done needs it ("see the tools of every enabled server", "one prompt that needs tools from two servers" from an external client) | Medium |
| 2 | Keep the chat API key | **Missing.** The dashboard mints a 1-day key per browser tab and keeps it in `sessionStorage` | Small |
| 3 | Production-like VPS (stand-out 1) | **Half done.** `values-production.yaml` and the guide exist. Nothing has been deployed to a real VPS, and there is no TLS or cert-manager yet | Medium (mostly ops) |
| 4 | Upgrades and rollback (stand-out 9) | **Mostly done** for our services: Helm revisions, commit-tagged images, migration Jobs, docs. The store's upgrade and rollback isn't documented or shown | Small |
| 5 | Security hardening (stand-out 6) | **Mostly done.** See the gaps below | Small |
| 6 | Observability (stand-out 8) | **Half done.** Prometheus `/metrics` exists on the gateway and agent. The dashboard has usage, error-rate and latency charts per server. Nothing scrapes the metrics yet | Small to medium |
| 7 | OAuth on the gateway (stand-out 3) | **Missing** | Large |
| 8 | Many tools (stand-out 4) | **Missing.** The agent sends every allowed tool, hard-capped at `AGENT_MAX_TOOLS=64` (the rest are silently dropped) | Medium |
| – | Scaling (stand-out 7) | **Mostly done**: stateless MCP, Postgres-backed rate limits per key and user, production values run 2 replicas. Needs a demo with 2 gateway replicas | Small |
| – | Multi-store provisioning (bonus) | Not started (skipped on purpose) | Large |
| – | Hosted MCP servers (stand-out 5) | **Done** (`docs/hosted-mcp.md`) | – |

Suggested order: **1 → 2 → 5 → 6 → 8 → 3/4 together → 7.** Items 1 and 2 unblock the demo. Items 5, 6 and 8 are cheap stand-outs. The VPS work comes once the features are frozen, and OAuth comes last because it is the largest and the least required.

---

## 1. One aggregated MCP endpoint

Add `/v1/mcp` (keep `/v1/mcp/{slug}` for single-server clients).

- **Listing:** for the key's team, resolve every enabled connector the caller has access to, list tools from all of them **in parallel** with a per-connector deadline, and merge the results. A slow or dead server is left out of the list (and logged and metered), so it never fails the whole list. This satisfies "a slow or dead upstream must not break tool listing".
- **Name collisions:** expose tools as `<slug>__<tool>` (the scheme the agent already uses, so both paths match). Allowed name characters keep `__` unambiguous because slugs cannot contain `_`. Prefix descriptions with `[slug]` so the model knows the source.
- **Calling:** split the name on the first `__`, then run the existing `ToolService.call_tool`. Authorization, audit, usage and rate limits stay exactly as they are.
- **Caching:** reuse the per-connector tool cache; send `notifications/tools/list_changed` only if we later add sessions (stateless for now).
- **Dashboard:** API keys page shows "MCP endpoint: `https://gateway…/v1/mcp`" with a copyable Claude Code / Cursor config snippet.
- **Tests:** merging, collisions, a dead upstream that is left out, and a member who sees only their grants.
- Document the naming scheme in the README "System design and tradeoffs" note, which the PDF requires anyway.

## 2. Persist the chat key

The root cause is that the agent only accepts platform keys (`urk_`), so the dashboard has to hold one. Options:

- **A. Recommended: an automatic dashboard key tied to the login session.** After login, the dashboard calls the existing key API once per team to create a key named `dashboard (auto)`, scoped `tools:read, tools:call`, expiring when the session expires (≤ 12 h). It is stored in `localStorage` under user+team, reused across tabs and reloads, and revoked on logout. A new key is created only when the stored one is missing, expired or revoked (a 401 from the agent). No pasting at all. To keep auto-keys from piling up, the gateway either revokes the previous `dashboard (auto)` key when it creates a new one, or adds a small "replace my dashboard key" endpoint.
- B. Just switch `sessionStorage` to `localStorage`. One line, but a long-lived key then sits in the browser forever. Not recommended.
- C. Let the agent accept the session token and exchange it for a short-lived key server-side. This is the cleanest security, but it changes the agent/gateway contract. Consider it together with OAuth (item 7).

## 3. Production-like VPS

- Hetzner CX22 or a free-tier ARM VM, k3s, one domain with `app.`, `gateway.` and `shop.` subdomains.
- cert-manager + Let's Encrypt `ClusterIssuer`, with TLS on all three ingresses. Store on HTTPS, so the MCP server switches to Basic auth over TLS automatically.
- Images in GHCR, built for `linux/amd64` by a small GitHub Actions workflow on tag. The VPS pulls with an `imagePullSecret`.
- Secrets: SOPS or Sealed Secrets in the repo (encrypted), or created by hand once. Keep Neon for the databases, or run Postgres in the cluster on `local-path` storage plus backups.
- Show the diff `values-local.yaml` vs `values-production.yaml` in the video (hosts, TLS, replicas, storage class, `APP_ENV`, docs off, pinned hosted-image digests).

## 4. Upgrades and rollback

- Already have: Helm revisions, `maxUnavailable: 0`, a migration Job before rollout, `helm rollback`, all documented in `docs/deployment.md` section 5.
- Add: a documented **store** upgrade (`helm upgrade store bitnami/wordpress --version …` with the volume kept, plus a MariaDB dump first) and its rollback limits (a database rollback needs the dump).
- Add: a short recorded drill. Deploy v2 of the gateway, watch the rolling update, `helm rollback`, and show no failed requests while it runs.
- Optional: `helm upgrade --atomic` in `deploy-local.sh` so a failed upgrade rolls itself back.

## 5. Security hardening: remaining gaps

Done: SSRF allow-list for connector URLs, no redirects, hashed keys, encrypted upstream credentials, non-root and read-only pods, least-privilege RBAC only for the gateway, deny-by-default in `urumi-mcp`, the restricted Pod Security Standard there, and an image allow-list.

To do (all small):
- Default-deny ingress **and egress** NetworkPolicy in `urumi`, then allow only DNS, Neon (443), Gemini (443) and the in-cluster paths explicitly.
- SSRF: also resolve the connector host and reject private, link-local and metadata IPs unless explicitly allow-listed (protects against an allow-list pattern that is too broad, and against DNS rebinding).
- Treat upstream tool descriptions as untrusted. Cap their length, strip control characters, and have the agent's system prompt say tool output is data. Add a dashboard warning when a server's tool list changes.
- `pod-security.kubernetes.io/enforce: restricted` on `urumi` too (the store may need `baseline`).
- Write the threat-model note the PDF asks for: shared credentials, no token passthrough, malicious upstreams, member-vs-member.

## 6. Observability

Already: request, tool, upstream-failure and rate-limit counters and histograms (gateway); run, LLM and token metrics (agent); usage charts in the dashboard; clear `upstream_*` error codes.

Recommendation: **yes, add Prometheus + Grafana, but lightweight.** It is worth it for the video.
- `kube-prometheus-stack` is heavy for a laptop. Use `prometheus-community/prometheus` (server only) plus `grafana/grafana`, or the stack with alertmanager and node-exporter turned off, in a `monitoring` namespace.
- Add an optional `ServiceMonitor` (or scrape annotations) to the `urumi-service` chart, off by default.
- One provisioned dashboard JSON in the repo: calls, error rate and p95 latency **per server**, upstream failures by reason, rate-limited requests, and agent run latency and tokens.
- On the MCP servers page, show *why* a server is unreachable (last probe error code and time) instead of only "Unreachable".
- Skip tracing for now; request IDs already correlate the agent, gateway and upstream logs.

## 7. OAuth on the gateway

Follow the MCP authorization spec so Claude Code and Cursor can connect with a browser login instead of a pasted key:
- The gateway becomes an OAuth 2.1 authorization server with PKCE: `/.well-known/oauth-authorization-server` and `/.well-known/oauth-protected-resource`, `/authorize` (reuses the dashboard login and a consent page), `/token`, and dynamic client registration (`/register`).
- The access token is a short-lived (≤ 1 h) opaque token, hashed like API keys and bound to user+team+scopes, with a refresh-token rotation. Internally it resolves to the same `Principal`, so authorization is unchanged.
- Unauthenticated requests to `/v1/mcp` return 401 with `WWW-Authenticate: Bearer resource_metadata=…`, so clients discover the flow.
- API keys stay for scripts and the agent. Reusing an existing library (Authlib) beats writing the protocol by hand. Largest item: do it last.

## 8. Tool selection when the catalog grows

Today every allowed tool is sent to the model, capped at 64, and the rest are silently dropped. Plan, in order of payoff:
1. **Meta-tools (the PDF's suggestion):** when the catalog is over a threshold (e.g. 30), the model gets only `search_tools(query)` (ranked name, description and server, top 10), `describe_tool(name)` (its schema) and `call_tool(name, arguments)`. The agent validates `call_tool` arguments against the real schema and still calls through the gateway with the user's key, so authorization is unchanged.
2. **Ranking:** start with BM25 or keyword scoring over name, description and server (no new infrastructure). Embeddings in Postgres with `pgvector` come later, if quality needs it.
3. **Pre-filtering:** a cheap first pass that picks relevant servers from the user's message and recent conversation, then exposes only their tools directly. Hybrid: always expose the last-used tools plus the meta-tools.
4. Make the cap visible: log and meter when tools are truncated, and show it in the chat's tools popover.
5. The same `search_tools` and `call_tool` pair can also be offered on the aggregated MCP endpoint (item 1) as an opt-in for external clients.
