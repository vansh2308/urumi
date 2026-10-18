# woocommerce-mcp

Upstream [MCP](https://modelcontextprotocol.io) server that exposes one WooCommerce store as MCP
tools over **Streamable HTTP**. It is meant to sit behind the platform gateway; it is not the
gateway.

| Tool | Kind | What it does |
| --- | --- | --- |
| `list_products` | read | Paged product list with search / SKU / status / stock / category filters |
| `create_product` | write | Creates a simple product (defaults to `draft`), idempotent with a key |
| `list_orders` | read | Paged order summaries with status / customer / date / search filters |
| `get_order` | read | One order with line items and totals |
| `update_order_status` | write | Sets an order status; no-op if already set, idempotent with a key |

## Security boundary

This service does **not** authenticate MCP clients, issue API keys or enforce user permissions.
The gateway authenticates platform users and decides which of them may call which tool. This
server trusts every request that reaches it, so:

* **Never expose `/mcp` publicly.** Run it as a `ClusterIP` Service with no Ingress, and add a
  NetworkPolicy that only admits traffic from the gateway pods. On a VPS, bind to a private
  interface or localhost behind the gateway.
* Network isolation reduces exposure but does not replace authorization at the gateway.
* The WooCommerce consumer key carries the permissions of the WordPress user it was generated
  for. Create a dedicated user and a key with the narrowest permission (read/write) you need.
* DNS-rebinding protection is on: only `Host` headers in `MCP_ALLOWED_HOSTS` are accepted.

Outputs are deliberately narrowed (no phone numbers, addresses, IPs, payment metadata or
`meta_data`) and credentials never appear in logs or errors. `httpx` request logging is silenced
because OAuth1 puts the consumer key and signature in the query string.

## Architecture

```
src/woocommerce_mcp/
  __main__.py        process entry point (uvicorn, graceful shutdown)
  app.py             ASGI app: /mcp + /healthz + /readyz, lifespan
  mcp_server.py      transport: FastMCP tools, argument schemas, error translation
  container.py       composition root (dependency injection, resource ownership)
  config.py          pydantic-settings, grouped by concern
  services/          use cases: products, orders, idempotent operation runner
  woocommerce/       REST client, auth (OAuth1 / Basic), typed models (no MCP imports)
  db/                SQLAlchemy models, engine, operation store
  errors.py          application exceptions with safe public messages
  log.py             JSON logging with per-request context
alembic/             migrations
```

* **Transport** (`mcp_server.py`) parses arguments, calls a service and returns its typed result.
  `AppError`s become MCP tool errors shaped `<code>: <message>` (with `(retryable)` when a retry
  may help). Anything unexpected becomes `internal_error` with a reference id; details stay in
  the logs.
* **Services** hold the rules: page-size limits, allowed status transitions (`refunded` must go
  through the refunds API, so it cannot be set here), idempotency and outcome tracking.
* **WooCommerce client** owns HTTP, auth, timeouts, retries and response validation. It takes
  an injected `httpx.AsyncClient` and has no dependency on MCP.
* **Persistence** stores one row per write in `operations` (key, request hash, status, resulting
  resource id, constrained result for replay). It is operational metadata with a retention
  period, not an audit log; platform-wide auditing belongs to the gateway.

All objects are built once in `container.py` and passed explicitly. There are no module-level
clients; the only per-request state lives in function locals and `contextvars`, so concurrent
requests never share mutable state.

### Authentication to WooCommerce

WooCommerce picks the scheme from the connection, so this server derives it from `WC_BASE_URL`:

* `http://` → one-legged **OAuth 1.0a**, parameters signed into the query string
  (HMAC-SHA256 by default). This is how the local store works. It requires
  `WC_ALLOW_INSECURE_HTTP=true` and is refused when `APP_ENV=production`.
* `https://` → **HTTP Basic** with the consumer key/secret.

`WC_BASE_URL` must match the WordPress *home* URL exactly (scheme, host and port), because the
store recomputes the OAuth signature over its own URL. Redirects are never followed.

### Retries, timeouts and idempotency

| Failure | GET / status update (idempotent) | create (non-idempotent) |
| --- | --- | --- |
| Connect error / connect timeout | retried | retried (nothing was sent) |
| HTTP 429 | retried, honours `Retry-After` | retried |
| Read timeout, dropped connection, 5xx | retried | **`outcome_unknown`**, not retried |
| HTTP 4xx | `upstream_rejected` / `not_found` / `upstream_auth_failed` | same |

Retries use capped exponential backoff with full jitter (`WC_MAX_ATTEMPTS`, default 3 attempts).
Every attempt is re-signed with a fresh OAuth nonce.

Write tools take an optional `idempotency_key` (8 to 128 chars of `A-Za-z0-9._:-`). With a key:

* Same key, same arguments, after success → the stored result is returned; WooCommerce is not
  called again.
* Same key, different arguments → `idempotency_conflict`.
* Same key while the first call is still running → `operation_in_progress (retryable)`.
* After a definite failure (store rejected it) → executed again.
* After an ambiguous failure (timeout after sending, crash) → `update_order_status` executes
  again (it is naturally idempotent); `create_product` returns `outcome_unknown`, because
  re-sending could create a duplicate. Check the store, then use a new key.

Races between replicas are settled by the unique `(operation, idempotency_key)` constraint and
compare-and-set updates; no DB connection is held while WooCommerce is being called. Records
expire after `OPERATIONS_RETENTION_HOURS` and are purged in the background.

### Health

* `GET /healthz` — liveness, no dependencies.
* `GET /readyz` — PostgreSQL reachable and migrated; returns 503 otherwise. Set
  `MCP_READINESS_CHECKS_WOOCOMMERCE=true` to also require the store; it is off by default so a
  store outage does not pull every replica out of the Service.

### MCP transport

Streamable HTTP at `MCP_PATH` (default `/mcp`), **stateless** with JSON responses by default:
the tools need no server-initiated messages, so any replica can serve any request and no sticky
sessions are needed. Set `MCP_STATELESS_HTTP=false` if session-bound features are added later.

### Shutdown

On SIGTERM uvicorn stops accepting connections, waits up to `MCP_SHUTDOWN_GRACE_SECONDS`
(default 25) for in-flight requests, then the lifespan stops the MCP session manager, the purge
task, the HTTP client pool and the DB pool. A write interrupted by shutdown is recorded as
`unknown`. In Kubernetes keep `terminationGracePeriodSeconds` above the grace period (e.g. 30)
and give the readiness probe `timeoutSeconds: 5`.

## Running locally

Requires Python 3.12+ (developed on 3.14), Poetry 2 and PostgreSQL.

```bash
cd services/woocommerce-mcp
poetry install
cp .env.example .env        # fill in the WooCommerce key/secret and DB_URL
poetry run alembic upgrade head
poetry run woocommerce-mcp  # serves http://127.0.0.1:8000/mcp
```

On macOS, keep `WC_IP_FAMILY=ipv4` for `*.local` store names: the system resolver spends about
5 s on an IPv6 mDNS lookup before using `/etc/hosts`, which would exhaust the connect timeout
and surface as `upstream_unavailable`.

Generate the key under *WooCommerce → Settings → Advanced → REST API* for the local store at
`http://wordpress.local`. Try the tools with the MCP Inspector
(`npx @modelcontextprotocol/inspector`, transport *Streamable HTTP*, URL
`http://127.0.0.1:8000/mcp`).

Migrations only need `DB_URL`:

```bash
poetry run alembic upgrade head
poetry run alembic revision -m "describe change"   # new migration, then edit it
```

## Configuration

All settings are environment variables (a `.env` file in the working directory is also read).
See [.env.example](.env.example) for the full list. Required: `WC_BASE_URL`,
`WC_CONSUMER_KEY`, `WC_CONSUMER_SECRET`, `DB_URL`. Invalid configuration exits with status 2 and
a message that never includes the offending values.

## Development

```bash
poetry run ruff format .
poetry run ruff check .
poetry run mypy
```

## Tests

The suite never touches the real store or the real database. WooCommerce is an in-process fake
(`httpx.MockTransport`), and PostgreSQL tests create a throwaway database on a local server named
by `TEST_DATABASE_URL`, migrate it, and drop it afterwards. They are skipped when the variable is
unset and refuse any host other than `localhost`/`127.0.0.1`.

```bash
docker run -d --rm --name urumi-test-postgres -e POSTGRES_PASSWORD=postgres \
  -p 127.0.0.1:55432:5432 --tmpfs /var/lib/postgresql/data postgres:16-alpine
TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55432/postgres poetry run pytest
```

What is covered: OAuth 1.0a signing against a transcription of WooCommerce's verifier, the retry
policy (reads retried, ambiguous creates never re-sent), error mapping, idempotent writes and
their concurrency on PostgreSQL, and the ASGI app end to end over Streamable HTTP with the
official MCP client (tools, structured results, safe tool errors, probes, Host-header checks).
