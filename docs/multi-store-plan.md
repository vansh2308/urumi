# Multi-store provisioning: plan and impact

Checked against local `master` (`d776433`, 2026-10-20) and the bonus section of `requirements.pdf`. Nothing here is implemented; this is the plan only.

**One-line summary:** add a `provisioner` worker (same codebase and image as the gateway, its own ServiceAccount) that turns a `stores` row into a Helm release of a new `urumi-store` chart in its own namespace, then registers the store's own WooCommerce MCP server as a team-owned connector. The gateway, agent and dashboard keep working as they are; the current store stays as the "core" store.

## What the PDF asks for, and where each piece lands

| Requirement | Plan | Where |
| --- | --- | --- |
| Create / delete from the dashboard; list with Provisioning / Ready / Failed, URL, created time; several at once | `stores` table + REST API on the gateway, new **Stores** page, a worker pool (3 at a time) in the provisioner | gateway, frontend, provisioner |
| Helm or Kubernetes API, own namespace, own DB, Secrets, PVs, Ingress host | One Helm release per store (`urumi-store` chart: Bitnami WordPress + MariaDB, our MCP server, policies) in namespace `store-<slug>` | `deploy/helm/urumi-store`, provisioner |
| Own WooCommerce MCP server, registered automatically, tool names show the store | Each release runs the existing `urumi/woocommerce-mcp` image; the provisioner inserts a `kind=store` connector with slug `<store-slug>` → tools `acme__list_orders`, descriptions prefixed `[Store: Acme]` | provisioner, gateway |
| Clean teardown | Unregister connector first (tools vanish), `helm uninstall`, delete namespace (PVCs, Secrets go with it), drop the store's MCP DB schema | provisioner |
| Isolation shown with NetworkPolicies | Default-deny per store namespace, plus allow-rules only for its own pods; a scripted proof | `urumi-store` chart, docs |
| ResourceQuota + LimitRange, max stores per user, provisioning timeout | Quota/LimitRange created with the namespace; per-user cap enforced in the DB; `deadline_at` per store | provisioner, gateway |
| Safe to retry; restart mid-provisioning completes or fails cleanly | Idempotent create (unique name per team + `Idempotency-Key`), DB as source of truth, step-wise reconciler with a lease, `helm upgrade --install` | gateway, provisioner |

---

## 1. Architecture

```
dashboard ──► gateway  POST /v1/teams/{team}/stores   (writes row: status=requested)
                          │
                    Postgres (gateway DB)  ◄── single source of truth
                          │
               provisioner (1 replica, leader lease)  ── polls rows, runs steps ──►  Kubernetes API + helm
                          │                                                         namespace store-acme
                          └── on ready: insert connector(kind=store) + team link      ├─ wordpress + mariadb (PVCs)
                                                                                      ├─ store-mcp (woocommerce-mcp image)
gateway ──(NetworkPolicy-allowed)──► store-mcp.store-acme.svc:8000/mcp ──► wordpress   ├─ Secrets, Ingress acme.<domain>
                                                                                      └─ NetworkPolicies, Quota, LimitRange
```

### Why a separate provisioner, not the gateway itself

- **Privilege.** Creating and deleting namespaces needs cluster-scoped RBAC. The gateway is internet-facing; today its only Kubernetes rights are a Role in `urumi-mcp`. Keeping cluster rights in a pod with no Ingress and no inbound traffic keeps the blast radius small.
- **Replicas.** Production runs 2 gateway replicas. A background reconciler inside the gateway would run twice. The provisioner runs 1 replica and also takes a Postgres advisory lock, so even a rolling update never has two leaders.
- **Long work.** WordPress first boot takes 2–5 minutes. That should not live in an HTTP request.
- **Reuse.** Same Poetry project and image as the gateway (`gateway-provisioner` entry point), so it reuses the DB models, `KubeApi`, audit log and connector code. No new image to build.

### Helm vs raw Kubernetes API

**Recommended: Helm, one release per store**, using a vendored chart baked into the image (no chart download at runtime). It matches the PDF's wording, `helm list -A` is a nice demo shot, uninstall is one command, and it reuses the Bitnami chart we already run. The provisioner shells out to a pinned `helm` binary.

The known Helm pitfall is a release stuck in `pending-install` when the process dies mid-install. Mitigations: the provisioner never waits inside Helm (`--wait=false`, it polls readiness itself, so a Helm call lasts seconds), and on startup a `pending-*` release for a store we own is rolled back or uninstalled and retried once (see §5).

Alternative: `helm template` plus server-side apply through `KubeApi` (fully restart-safe, but Bitnami's `lookup`-based password reuse breaks and `KubeApi` would need StatefulSet, PVC, Ingress, Job kinds). Not worth it.

---

## 2. The `urumi-store` chart (new, `deploy/helm/urumi-store`)

Dependencies: `bitnami/wordpress` (pinned, same version as today, 34.1.3, with its MariaDB) and our `urumi-service` chart aliased as `store-mcp`.

Templates of its own:
- **NetworkPolicies** (see §6).
- **Setup Job** `store-setup` (WP-CLI, same image as WordPress), run after WordPress is ready, idempotent so it can run again:
  - activate WooCommerce (baked into the image, see below), skip the onboarding wizard
  - pretty permalinks (the REST API path the MCP server uses), store country/currency, enable **Cash on delivery**
  - create the WooCommerce REST key from values the provisioner generated: insert into `wp_woocommerce_api_keys` with `consumer_key = wc_api_hash(ck)` and the secret, `read_write`, owned by the admin user. The provisioner already knows the plain `ck_…/cs_…`, so it never has to read anything back from WordPress.
  - optional demo seed: 3 products, so a fresh store is not empty in the video.
- Values set per store by the provisioner: host, store name, Secret names, sizes, resources.

**Store image.** Bitnami downloads plugins from wordpress.org at first boot, which is slow and needs internet egress from the store namespace. Build `urumi/store-wordpress` (`FROM` the pinned Bitnami WordPress image, WooCommerce zip copied in). Faster, works offline, and the store namespace's egress can stay closed. Note: Bitnami moved most images to the `bitnamilegacy` repo in 2025; pin by digest whichever repo the current store uses.

**Secrets, all generated by the provisioner** (never in values files or the DB): WordPress admin password, MariaDB root and user passwords (`existingSecret` in the Bitnami values), WooCommerce `ck_/cs_`, the MCP server's `DB_URL`. The WordPress admin password is shown once in the dashboard on creation (same pattern as API keys) so the user can log into wp-admin.

**No `helm.sh/resource-policy: keep`** on store PVCs, unlike the core store's values: here teardown must remove volumes.

### The MCP server per store

Same `urumi/woocommerce-mcp` image, two small code changes:

1. **Internal URL vs signing URL.** Today the MCP pod reaches WordPress through `WC_BASE_URL=http://wordpress.local` pinned to the Service IP with `hostAliasesFromServices` (OAuth 1.0a signatures are over the canonical URL). That trick needs the Service IP at render time. Add `WC_CONNECT_URL` (e.g. `http://store-wordpress.store-acme.svc.cluster.local`) used for the TCP connection, while signing and the `Host` header use `WC_BASE_URL` (the store's public URL). The core store can keep its current setup.
2. **Store identity.** `STORE_NAME` env: used in the MCP server's `name`/instructions and tool descriptions ("Lists orders of the Acme store").

`MCP_ALLOWED_HOSTS` gets the per-store Service names. `DB_URL`: see §7 for the operations DB.

---

## 3. Gateway changes

**Migration `0009_stores`:**
- `stores`: `id, team_id (FK, RESTRICT), created_by_user_id, name, slug (unique, never reused), namespace, release, host, status, status_reason, step, attempts, deadline_at, lease_owner, lease_expires_at, connector_id (nullable), created_at, ready_at, deleted_at`. Unique `(team_id, name)` among non-deleted stores.
- `connectors.kind` gains `store`; `connectors.owner_team_id` (nullable, set for store connectors).

**API** (team-scoped, same auth as today, audited):
- `GET /v1/teams/{team}/stores` – list with status, URL, created time, reason.
- `POST /v1/teams/{team}/stores` – `{name}` plus optional `Idempotency-Key`; returns 202 with the row. Same key or same name for an existing non-failed store returns that store (retry-safe).
- `DELETE /v1/teams/{team}/stores/{id}` – marks `deleting`, 202. Deleting twice is fine.
- `POST /v1/teams/{team}/stores/{id}/retry` – for a `failed` store: clean up, then provision again.

**Rules:**
- Who may create: **any team member** (the PDF says "a user"), capped at `STORES_MAX_PER_USER` (default 2) and `STORES_MAX_TOTAL` (default 5 locally). The count and the insert happen in one transaction holding a row lock on the user, so two parallel requests cannot both pass the cap. Delete: the creator or a team admin.
- Access: the store's connector is linked to the owning team only (`team_connectors`, all tools). The creator gets a `member_tool_grant`; team admins see it as usual; admins can grant other members the normal way.
- Store connectors are **managed**: the platform registry API refuses edit/delete for `kind=store` ("delete the store instead"). Enable/disable still works (that is the "disable a server" story).
- SSRF: store connector URLs are generated by the platform, so they get the same exemption as `hosted` (`verify_destination=False` for both kinds).
- Slugs: `[a-z][a-z0-9-]{1,20}`, no underscore (keeps `__` unambiguous), short enough that `slug__update_order_status` stays under the 64-character function-name limit in the agent. Deleted slugs are kept (`deleted_at`) and never reused, so the audit log and usage history stay unambiguous.

---

## 4. Provisioner state machine

Each store moves through steps; every step is idempotent, and the current step is saved after it succeeds:

| Step | Action | Done when |
| --- | --- | --- |
| `namespace` | Server-side apply namespace `store-<slug>` with labels `urumi.io/store=<id>`, `urumi.io/managed-by=provisioner`, PSS `baseline` enforce + `restricted` warn; then ResourceQuota, LimitRange, default-deny NetworkPolicy | objects exist |
| `secrets` | Create the Secrets **only if missing** (a retry must not rotate a DB password MariaDB already initialised with) | Secrets exist |
| `mcp-db` | Create the store's Postgres role + schema (§7) | `CREATE … IF NOT EXISTS` succeeded |
| `release` | `helm upgrade --install store <chart> -n store-<slug> --wait=false -f <generated values>` | release `deployed` |
| `wait-ready` | Poll WordPress, MariaDB, store-mcp readiness; read pod waiting reasons like the hosted code does (`ImagePullBackOff`, `CrashLoopBackOff` → fail fast with that reason) | all Ready |
| `setup` | Create the `store-setup` Job (name includes the attempt number), wait for completion | Job succeeded |
| `register` | One DB transaction: insert connector (`kind=store`, URL `http://store-mcp.store-<slug>.svc.cluster.local:8000/mcp`), team link, creator grant, `status=ready`, audit event; then probe it once | status `ready` |

Status shown in the dashboard: `requested`/any step → **Provisioning** (with the step name as detail), `ready` → **Ready**, `failed` → **Failed** + reason, `deleting` → **Deleting**.

**Delete** runs: disable + delete connector rows (tools disappear from the agent and `/v1/mcp` at once) → `helm uninstall` → delete namespace → wait until the namespace is gone (PVCs and their local-path volumes are removed) → drop the MCP schema and role → `status=deleted`. Each step tolerates "already gone".

**Concurrency.** A worker pool of 3. A worker claims a row with `SELECT … FOR UPDATE SKIP LOCKED` and sets a lease (`lease_owner`, `lease_expires_at`, renewed every 15 s). Different stores have different namespaces and Helm releases, so parallel installs do not interfere.

## 5. Timeouts and recovery

- `deadline_at = created_at + STORE_PROVISION_TIMEOUT` (15 min locally, 10 min in production). Past it, the store becomes `failed` with a reason naming the step ("timed out waiting for WordPress to become ready: 0/1 pods ready, last event: …"), and its resources are torn down. The row stays so the user sees why; **Retry** or **Delete** are offered.
- **Provisioner restart.** On start (and every 30 s) it picks up rows whose lease expired and resumes from the saved step. Because every step is "make it so" (apply, create-if-missing, `upgrade --install`, poll), re-running a step is harmless. Special case: a Helm release in `pending-install`/`pending-upgrade` (Helm died mid-call) is rolled back or uninstalled, then the step is retried; after `STORE_MAX_ATTEMPTS` (3) the store fails cleanly.
- **Gateway restart.** Nothing to do: the gateway only writes rows.
- **Retry of create.** Same name or same `Idempotency-Key` returns the existing store instead of creating a second one.
- **Drift.** A periodic check (every 5 min) marks a `ready` store whose namespace or release vanished as `failed: resources missing`.

## 6. Isolation (NetworkPolicies)

Per store namespace, default deny in both directions, then:

| Pod | Ingress from | Egress to |
| --- | --- | --- |
| WordPress | Traefik (`kube-system`), store-mcp and setup Job in the **same** namespace | MariaDB (same ns), DNS |
| MariaDB | WordPress and setup Job (same ns) | none |
| store-mcp | gateway pods in `urumi` only | WordPress (same ns), DNS, public 5432 (Neon) |
| setup Job | none | WordPress, MariaDB (same ns), DNS |

The `urumi` gateway policy gets one new egress rule: namespaces labelled `urumi.io/managed-by=provisioner`, pods labelled `app.kubernetes.io/name=store-mcp`, port 8000.

**Proof for the video** (`docs/stores.md`, scripted): from store A's MCP pod, `curl` store B's WordPress Service and `nc` store B's MariaDB both time out; from store A's MCP pod its own WordPress answers; from the agent pod, store A's MCP is unreachable (only the gateway may call it).

The MCP server's Neon DB is the one thing reachable outside the cluster, which is why each store gets its own Postgres role that can see only its own schema (§7).

## 7. Guardrails and RBAC

- **ResourceQuota per store** (fits Bitnami defaults with a little room): `pods 6, requests.cpu 1, requests.memory 1.5Gi, limits.memory 3Gi, persistentvolumeclaims 2, requests.storage 6Gi, services 5, secrets 10`. **LimitRange** defaults for containers that set nothing.
- **Max stores** per user and in total (§3), plus the provisioning timeout (§5).
- **Provisioner RBAC:** a ClusterRole that can create/delete namespaces, and a ClusterRole bound per store namespace (RoleBinding created in the `namespace` step) for the objects Helm creates there. Kubernetes RBAC cannot filter by label, so add a **ValidatingAdmissionPolicy** (GA in 1.30; k3s ships it) that lets the provisioner's ServiceAccount delete or modify only namespaces labelled `urumi.io/managed-by=provisioner`. Without it, a bug could delete `urumi`.
- The provisioner reaches the API server through the same per-environment egress rule the gateway uses today (`extraEgress` 6443).
- **Operations DB of each store's MCP server.** The MCP server keeps idempotency records (with tool results, i.e. order data) in Postgres. Sharing today's `DB_URL` would let store A's MCP pod read store B's records. The provisioner holds an admin DSN for the MCP database (provisioner-only Secret) and creates `role store_<id>` + `schema store_<id>`, owned by that role, with `search_path` set on the role. The store's migration Job runs Alembic inside that schema. Teardown drops both. (Alternative if this proves fiddly on Neon: a tiny Postgres StatefulSet per store, which costs ~128 Mi more per store.)

---

## 8. Dashboard

- **Stores** page: table (name, status badge with step detail, URL link, created time, created by, actions), "Create store" dialog (name only), "Delete" with type-the-name confirmation, "Retry" on failed rows, polling every 5 s while anything is provisioning.
- Store detail: storefront and wp-admin links, the MCP connector it registered (link to the server page), recent events, the one-time admin password on creation.
- MCP servers page: store connectors shown with a "Store" badge and edit/delete hidden.
- Chat: nothing to change; a new store's tools appear on the next message.

## 9. Domains

- **Local:** per-store host `<slug>.localtest.me` (public DNS resolves every `*.localtest.me` to 127.0.0.1), so no `/etc/hosts` edit per store, and Traefik on host port 80 already routes it. Inside the cluster the MCP pod never uses that name (it connects with `WC_CONNECT_URL`, §2). Fallback if you are offline: `dnsmasq` with `address=/stores.urumi.local/127.0.0.1`.
- **VPS:** wildcard DNS `*.shop.example.com` → the VPS IP; cert-manager issues one certificate per store host by HTTP-01 (or one wildcard by DNS-01). The base domain is a provisioner Helm value (`STORE_BASE_DOMAIN`, `STORE_TLS`), so local vs VPS is values only, as the PDF requires.

---

## 10. What will break (or needs changing) in the current project

Grouped by how much it hurts. "Breaks" means it fails or misbehaves as soon as a second store exists if we change nothing.

### Breaks outright

1. **Gateway cannot reach a store's MCP server.** The gateway's egress NetworkPolicy (`values.yaml`) allows only `woocommerce-mcp` pods in its own namespace and `hosted-mcp` pods in `urumi-mcp`. Calls to `store-*` namespaces are dropped, so every store connector shows **Unreachable**. Needs the new egress rule (§6).
2. **SSRF guard rejects store URLs.** `UPSTREAM_ALLOWED_HOSTS` is one exact host, and private (ClusterIP) addresses are allowed only for hosts named exactly, with no wildcard. Per-store hosts are dynamic, so registration and every call fail with "destination blocked". Needs the `kind=store` exemption, like `hosted` (`services/connectors.py:217`).
3. **The gateway's Kubernetes client is single-namespace.** `KubeApi` builds every path from one fixed namespace (`HOSTED_NAMESPACE`) and has no Namespace, Job, PVC or quota kinds; its Role exists only in `urumi-mcp`. Provisioning cannot be bolted onto it as is; the provisioner needs its own client config and cluster RBAC (§7).
4. **Store credentials are made by hand.** Today the WooCommerce REST key is created in wp-admin and copied into `services/woocommerce-mcp/.env`, and WooCommerce itself was installed by hand. None of that works for a store nobody has logged into. Needs the setup Job and the provisioner-generated keys (§2).
5. **The MCP server's `hostAliasesFromServices` trick does not work per store.** It needs the store Service's IP when the chart renders and a `/etc/hosts`-style canonical name. Needs `WC_CONNECT_URL` (§2), a small change in `woocommerce_mcp/config.py` and its HTTP client.
6. **Shared operations DB leaks across stores.** All MCP servers would share one `DB_URL` and one `operations` table with no store column: one store's idempotency keys and cached results (order data) are visible to another store's MCP server, and keys could collide. Needs a role and schema per store (§7).

### Wrong or unsafe, but does not crash

7. **Every user sees every store.** `list_registry` returns all enabled connectors to any user (`services/connectors.py:290`). With team-owned stores, a member of team B would see team A's store names and URLs. Filter by `owner_team_id` for non-platform-admins.
8. **Platform admins could edit or delete a store connector** through the normal registry API, leaving a running namespace with no connector, or a connector pointing at a deleted store. Needs the "managed" guard (§3).
9. **Tool cap in the agent.** `AGENT_MAX_TOOLS=64` and extra tools are dropped silently. GitHub MCP alone exposes dozens of tools; with GitHub + DuckDuckGo + 3 stores the store tools may be the ones cut, and "compare orders across two stores" fails in the video. Short term: limit GitHub tools for the demo team (`allowed_tools`) or raise the cap; proper fix is bonus item 8 (search/execute meta-tools).
10. **Tool-name length.** Connector slugs may be 63 characters today; `slug__update_order_status` can exceed the 64-character function-name limit in the agent's `toolbox.py`, which then truncates names. Store slugs capped at 21 characters (§3).
11. **External MCP clients keep a stale tool list** after a store is deleted: the gateway is stateless and does not send `tools/list_changed`, so Claude Code/Cursor still show the tools until they reconnect (calls then fail with `not_found`). Acceptable, but say it in the demo and the docs; the agent and dashboard update at once.
12. **Slug reuse muddles history.** `tool_invocations` and audit events keep `connector_slug` as a string. If a deleted store's slug is reused, old usage looks like the new store's. Never reuse store slugs (§3).

### Ops, deploy and docs

13. **Laptop resources.** Each store is about 1 GB of memory (WordPress ~512 Mi, MariaDB ~256 Mi, MCP ~128 Mi) and two 2 Gi local-path volumes. With the core store, the platform and two hosted servers, three parallel stores need roughly 6 GB for Docker Desktop. Set `STORES_MAX_TOTAL` per environment and document the Docker memory setting.
14. **Pod Security.** `deploy-local.sh` enforces `restricted` on `urumi`. New namespaces get labels from the provisioner (WordPress meets `restricted` per the current comment; WP-CLI Job must too, else use `baseline`). Test once before relying on it.
15. **Deploy scripts and charts.** `urumi` umbrella chart gains a `provisioner` component (same image, other command), its RBAC, the admission policy and the vendored `urumi-store` chart; `deploy-local.sh` must build and import `urumi/store-wordpress`; `push-images.sh` and `values-production.yaml` gain the new image and `STORE_BASE_DOMAIN`/TLS settings; `create-local-secrets.sh` adds the provisioner's admin DSN for the MCP database.
16. **Backups.** `backup-store.sh` dumps only the core store in `urumi`. Provisioned stores are not backed up. Document that they are disposable demo stores, or extend the script to loop over `store-*` namespaces.
17. **Team deletion.** `stores.team_id` is `RESTRICT`, so deleting a team with live stores now fails with a clear error instead of orphaning namespaces. Intended, but the dashboard must explain it.
18. **Tests.** The gateway `conftest.py` fakes have no Kubernetes or Helm; the provisioner needs its own fakes (fake `helm` runner, fake `KubeApi`) and the state-machine tests: retry, restart at each step, timeout, quota cap race, teardown with missing objects.

### What does *not* break

- The **core store** (`store` release in namespace `urumi`, `http://wordpress.local`) and its `woocommerce` connector stay exactly as they are. It is the "first registered server" the core user stories need. The Stores page can show it as a read-only "Core store" row, but the provisioner never touches it.
- Hosted MCP servers, OAuth, API keys, audit log, rate limits and the `<slug>__<tool>` naming all work unchanged; a store connector is just another connector to them.

---

## 11. Milestones (one commit each, on `feat/multi-store`)

| # | Milestone | Size |
| --- | --- | --- |
| 1 | `woocommerce-mcp`: `WC_CONNECT_URL` + `STORE_NAME`, tests | S |
| 2 | `urumi/store-wordpress` image + `urumi-store` chart (WordPress, MariaDB, store-mcp, policies, quota, setup Job); install one by hand with `helm` to prove it | L |
| 3 | Gateway migration `0009`, stores API, per-user cap, `kind=store` connectors (SSRF exemption, managed guard, team filter) | M |
| 4 | Provisioner: state machine, leases, Helm runner, per-store MCP schema, teardown, timeout, tests | L |
| 5 | Helm: provisioner component, RBAC, admission policy, gateway egress rule, values for local and VPS | M |
| 6 | Dashboard Stores page and detail | M |
| 7 | `docs/stores.md`: design, isolation proof script, recovery drill (kill the provisioner mid-install), demo script | S |

Rough total: 4–6 working days. Milestones 1–2 are the riskiest (WooCommerce setup without a browser) and can be validated on their own before any gateway work.

## 12. Demo script (the PDF's test)

1. Create stores **Acme** and **Globex** at the same time; both go Provisioning → Ready.
2. Place an order by hand on each storefront.
3. Chat: "Compare today's orders across Acme and Globex" → calls `acme__list_orders` and `globex__list_orders`.
4. Show isolation: the scripted cross-store `curl`/`nc` checks fail.
5. Delete Globex; show `kubectl get ns`, `kubectl get pvc -A`, `helm list -A` without it, its tools gone from the chat's tool list and from the MCP servers page, and the audit log entries for create and delete.
6. Optional: kill the provisioner pod while a third store is provisioning; it resumes and finishes (or fails with a reason).

## Open choices (defaults used above)

- **Who can create stores:** any member, 2 per user (default) vs team admins only.
- **Store MCP database:** role + schema per store in the shared Postgres (default) vs a small Postgres pod per store.
- **Local domains:** `*.localtest.me` (default, needs internet DNS) vs dnsmasq.
