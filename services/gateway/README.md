# gateway

The platform's control plane and secure entry point. It authenticates users and API keys, owns
teams, roles, invitations and platform API keys, keeps the registry of upstream MCP servers
("connectors"), and forwards only authorized tool calls to them. It contains no WooCommerce
logic: tools are discovered from and executed by the upstream servers such as
[`woocommerce-mcp`](../woocommerce-mcp).

```
MCP client / app ──Bearer urk_…──▶ gateway ──MCP Streamable HTTP──▶ woocommerce-mcp ──OAuth1──▶ store
dashboard user ──Bearer ugs_…──▶ gateway (teams, keys, policies, audit, usage)
```

For a step-by-step manual test, see [POSTMAN.md](POSTMAN.md) and the importable
[collection](postman/urumi-gateway.postman_collection.json).

## Responsibilities

| Owns | Does not own |
| --- | --- |
| Users, password login, session tokens | WooCommerce (or any) business logic |
| Teams, admin/member roles, invitations | WooCommerce consumer keys or other downstream credentials |
| Platform API keys: issue, scope, expire, rotate, revoke | Idempotency of writes (the upstream does it; pass `idempotency_key`) |
| Connector registry and routing by slug | |
| Team tool policies and member grants | |
| Tool discovery and authorized invocation (REST and MCP) | |
| Audit log, usage records, rate limits, metrics | |

## Credentials

| Prefix | What | Used for | Stored as |
| --- | --- | --- | --- |
| `ugs_` | user session token from `POST /v1/auth/login` | management API, discovery | HMAC-SHA256 |
| `urk_` | platform API key | discovery (`tools:read`), invocation (`tools:call`), own usage (`usage:read`) | HMAC-SHA256 |
| `uri_` | invitation token | joining a team once | HMAC-SHA256 |
| `uoq_` / `uoc_` / `uor_` | OAuth request id / authorization code / refresh token | MCP clients signing in with OAuth ([docs/oauth.md](../../docs/oauth.md)); access tokens are ordinary `urk_` keys that expire in an hour | HMAC-SHA256 |

All three are 256-bit random values shown exactly once; the HMAC uses `AUTH_TOKEN_PEPPER`, so a
database dump cannot be used to test guesses. Passwords use Argon2id. **Platform API keys are not
WooCommerce keys**: the gateway never sees, stores or forwards the store's `ck_`/`cs_`
credentials, which stay inside `woocommerce-mcp`.

**Connector credentials** are the other direction: an optional static header (bearer token or
custom header) the gateway sends to one upstream, e.g. a GitHub token. They are encrypted at rest
with `UPSTREAM_CREDENTIAL_KEYS` (Fernet), sent only to that connector's own origin, and never
returned or logged; the API shows the header name only. See
[docs/hosted-mcp.md](../../docs/hosted-mcp.md).

API keys cannot reach management endpoints (teams, members, keys, policies, audit); those need a
user session. Tool invocation needs an API key, so every call is attributable to a named,
revocable key.

## Authorization model

* **Team membership** is loaded from the database on every request. Anything in another team
  answers `404`, so ids cannot be probed (no IDOR). Request bodies reject unknown fields, so a
  `role` or `is_platform_admin` field cannot be smuggled into a payload.
* **Team admins** manage members, roles, invitations, team settings, every team API key
  (list/revoke), connector policies, member grants, and see the team's audit log and usage.
  The last admin of a team cannot be demoted or removed.
* **Members** see their own profile and teams, the connectors and tools granted to them, and
  their own keys, usage and audit events. Whether they may create keys is a team setting.
* **Platform admins** (bootstrapped with `gateway-admin`, never through the API) manage the
  connector registry. The flag gives no access inside any team.
* **Effective tool access** = team policy ∩ member grant (admins skip the grant) ∩ API key
  `tool_restrictions` (if set). Changes apply to existing keys immediately.
* **API key rules**: every key expires (capped by the team's `max_key_ttl_days` and
  `API_KEY_MAX_TTL_DAYS`); keys are always issued to the caller; only the owner can rotate
  (rotation can keep the old key alive for up to `API_KEY_MAX_ROTATION_GRACE_HOURS`); removing
  a member revokes their keys in that team.

## MCP routing and SSRF

Clients name a connector by **slug**; the gateway looks up its URL in the registry. URLs are set
only by platform admins and must match `UPSTREAM_ALLOWED_HOSTS` (`host:port` patterns), and
redirects are never followed, so the gateway cannot be pointed at arbitrary hosts. Hosted servers
(deployed from an allow-listed image with `POST /v1/hosted-servers`) get a URL the gateway builds
from their in-cluster Service, which cannot be edited; so do provisioned stores' MCP servers
(`kind=store`, registered by the provisioner, visible to their own team only). On top of the host allow-list, the host is resolved at
registration and before every call: link-local and cloud metadata addresses are always refused,
and private or loopback addresses only when the host is named exactly (no wildcard) in the
allow-list, so a broad pattern or a later DNS change cannot reach internal services
(`upstream_blocked`). Tool titles and descriptions from upstreams are stripped of control and
invisible characters and capped in length before anyone sees them.

Calls go through the official MCP SDK client over Streamable HTTP on a shared connection pool,
with per-connector deadlines (`timeout_seconds`), a per-connector concurrency limit per replica
(`max_concurrency`; excess waits `UPSTREAM_QUEUE_TIMEOUT_SECONDS`, then `503 upstream_busy`) and
the gateway's `X-Request-ID` forwarded, so upstream logs share the gateway's ids. Upstream
failures become `upstream_unavailable` / `upstream_timeout` / `upstream_bad_response` /
`upstream_rejected` without exposing upstream addresses. A tool that reports its own error comes
back as a normal MCP result with `isError: true`. If the client disconnects, the upstream call is
cancelled and the usage row records `cancelled`.

Two ways to call tools, with identical checks, auditing and usage tracking:

* **REST**: `POST /v1/teams/{team_id}/connectors/{slug}/tools/{tool}/call` with
  `{"arguments": {...}}` returns the MCP `CallToolResult`.
* **MCP, all servers**: `/v1/mcp` is one stateless Streamable HTTP MCP server for MCP clients
  (Inspector, Claude Code, Cursor, agents). The API key selects the team; `tools/list` returns
  the permitted tools of **every** enabled server, fetched in parallel. Tools are named
  `<server>__<tool>` (slugs cannot contain `_`, so the first `__` always splits them), which
  keeps equal tool names on different servers apart, and each description starts with
  `[Server name]`. A server that fails or takes longer than
  `UPSTREAM_AGGREGATE_LIST_TIMEOUT_SECONDS` (10 s) is left out of that listing, logged and
  metered, so one dead upstream never breaks the list. `GET /v1/teams/{team_id}/tools` returns
  the same listing over REST plus the `unavailable` servers.
* **MCP, one server**: `/v1/mcp/{slug}` exposes one server with its original tool names.

## API

OpenAPI docs are served at `/docs` (`GATEWAY_DOCS_ENABLED`). Errors are always
`{"error": {"code", "message", "request_id", ...}}`.

| Area | Endpoints |
| --- | --- |
| Health | `GET /health/live`, `GET /health/ready` (PostgreSQL reachable and migrated), `GET /metrics` |
| Auth | `POST /v1/auth/register`, `POST /v1/auth/login`, `POST /v1/auth/logout`, `GET /v1/me` |
| Teams | `POST/GET /v1/teams`, `GET/PATCH /v1/teams/{team_id}` |
| Members | `GET /v1/teams/{team_id}/members`, `PATCH/DELETE /v1/teams/{team_id}/members/{user_id}` |
| Invitations | `POST/GET /v1/teams/{team_id}/invitations`, `DELETE …/invitations/{id}`, `POST /v1/invitations/accept` |
| API keys | `POST/GET /v1/teams/{team_id}/api-keys`, `GET …/{key_id}`, `POST …/{key_id}/revoke`, `POST …/{key_id}/rotate` |
| Registry | `POST/GET /v1/connectors`, `GET/PATCH/DELETE /v1/connectors/{id}`, `POST /v1/connectors/{id}/probe` |
| OAuth | `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource/v1/mcp`, `/register`, `/authorize`, `/token`, `/revoke` (MCP SDK handlers); `GET /v1/oauth/config`, `GET/POST /v1/oauth/requests/{id}[/decision]`, `GET/DELETE /v1/oauth/grants[/{id}]` |
| Hosted servers | `GET /v1/hosted-servers/settings`, `POST/GET /v1/hosted-servers`, `GET/PATCH /v1/hosted-servers/{id}`, `POST …/{id}/restart` |
| Stores | `GET /v1/stores/settings`, `GET/POST /v1/teams/{team}/stores` (`Idempotency-Key`), `GET/DELETE …/stores/{id}`, `POST …/stores/{id}/retry` ([docs/stores.md](../../docs/stores.md)) |
| Team connectors | `GET /v1/teams/{team_id}/connectors`, `PUT/DELETE …/connectors/{slug}`, `GET …/connectors/{slug}/catalog` |
| Grants | `GET /v1/teams/{team_id}/members/{user_id}/grants`, `PUT/DELETE …/grants/{slug}` |
| Tools | `GET /v1/teams/{team_id}/tools`, `GET /v1/teams/{team_id}/connectors/{slug}/tools`, `POST …/tools/{tool}/call`, `/v1/mcp`, `/v1/mcp/{slug}` |
| Audit & usage | `GET /v1/teams/{team_id}/audit-events`, `GET /v1/audit-events`, `GET /v1/teams/{team_id}/usage/invocations`, `GET /v1/teams/{team_id}/usage/summary` |

## Reliability and observability

* **Rate limits**: fixed one-minute windows in PostgreSQL (shared by all replicas, no Redis):
  per API key / session (`RATE_LIMIT_PER_PRINCIPAL_PER_MINUTE`, or the team's
  `rate_limit_per_minute`) and per IP and email on login/registration. `429` responses carry
  `Retry-After`; successful ones `X-RateLimit-*`.
* **Audit log** (`audit_events`): logins, registrations, every team/member/invitation/key/policy
  /grant/registry change, and authorization denials, with actor, key, request id and IP. Change
  events are written in the same transaction as the change.
* **Usage** (`tool_invocations`): one row per call with outcome, error code, latency and
  transport. Arguments and results are never stored.
* **Metrics** (`/metrics`, Prometheus): `gateway_http_requests_total` and
  `gateway_http_request_duration_seconds` by route template and status (error rate),
  `gateway_tool_invocations_total` / `_duration_seconds`, `gateway_upstream_failures_total`,
  `gateway_rate_limited_total`, `gateway_auth_failures_total`. Expose `/metrics` only inside the
  cluster.
* **Logs**: JSON lines with `request_id`; tokens, keys, passwords and bodies are never logged.
* **Retention**: a background task purges audit (`RETENTION_AUDIT_DAYS`), usage
  (`RETENTION_USAGE_DAYS`), expired sessions/invitations and old rate-limit windows.
* **Shutdown**: on SIGTERM uvicorn stops accepting connections and drains for
  `GATEWAY_SHUTDOWN_GRACE_SECONDS`, then the lifespan stops the MCP endpoint, the purge task,
  the upstream connection pool and the DB pool. Keep `terminationGracePeriodSeconds` above the
  grace period.

## Architecture

```
src/gateway/
  __main__.py      process entry point (uvicorn, graceful shutdown)
  app.py           FastAPI app, routes, middleware, lifespan
  container.py     composition root (dependency injection, resource ownership)
  config.py        pydantic-settings, grouped by concern
  api/             transport: routes, schemas, auth dependencies, MCP endpoint, middleware
  services/        rules: auth, teams, invitations, api_keys, connectors, tools, activity, audit
  upstream/        MCP SDK client for registered servers (no knowledge of users or teams)
  db/              SQLAlchemy models and engine
  security.py      Argon2id passwords, token issue/HMAC
  cli.py           gateway-admin (platform admin bootstrap)
  stores/          store requests (caps, slugs) and store-connector registration
  provisioner/     gateway-provisioner: store state machine, Helm runner, per-store Postgres roles
alembic/           migrations
```

Routes only parse input and call a service; services hold the authorization and business rules;
the upstream client and the database are injected. There are no module-level clients, and
per-request state lives only in function locals and `contextvars`.

## Running locally

Requires Python 3.12+ (developed on 3.14), Poetry 2 and PostgreSQL. Use a separate database
from `woocommerce-mcp` (the gateway uses its own `gateway_alembic_version` table, so sharing
one also works).

```bash
cd services/gateway
poetry install
cp .env.example .env              # set DB_URL and AUTH_TOKEN_PEPPER
poetry run alembic upgrade head
poetry run gateway                # serves http://127.0.0.1:8100
```

After registering your first user, make it a platform admin so it can register connectors:

```bash
poetry run gateway-admin grant-platform-admin you@example.com
```

## Deployment notes

* Expose only the gateway. Keep upstream MCP servers on `ClusterIP` Services with a
  NetworkPolicy that admits traffic from gateway pods only, and list their in-cluster names in
  both `UPSTREAM_ALLOWED_HOSTS` (gateway) and `MCP_ALLOWED_HOSTS` (upstream).
* Terminate TLS in front of the gateway and set `GATEWAY_FORWARDED_ALLOW_IPS` to the ingress
  addresses so client IPs (rate limits, audit) are correct.
* Put `DB_URL` and `AUTH_TOKEN_PEPPER` in a Secret. Rotating the pepper invalidates every
  session, API key and invitation.
* Consider `AUTH_ALLOW_SIGNUP=false` outside local development (invitation-only accounts).

## Development

```bash
poetry run ruff format .
poetry run ruff check .
poetry run mypy
```

## Tests

The suite never touches the real database or a real MCP server. PostgreSQL tests create a
throwaway database on a local server named by `TEST_DATABASE_URL`, migrate it and drop it
afterwards (they are skipped when it is unset and refuse non-local hosts); the upstream is a small
FastMCP server the tests start on a free localhost port.

```bash
docker run -d --rm --name urumi-test-postgres -e POSTGRES_PASSWORD=postgres \
  -p 127.0.0.1:55432:5432 --tmpfs /var/lib/postgresql/data postgres:16-alpine
TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55432/postgres poetry run pytest
```

What is covered: token hashing and the SSRF allow-list; registration, login and sessions; team
RBAC, cross-team isolation (404, not 403) and payload smuggling; API keys (shown once, stored as a
hash, scopes, tool restrictions, revocation, rotation, ownership); connector registration rules;
tool discovery and invocation over REST and the MCP endpoint; upstream failures; usage and audit
visibility.
