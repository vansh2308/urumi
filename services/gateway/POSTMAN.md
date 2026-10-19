# Testing the gateway in Postman

The collection [postman/urumi-gateway.postman_collection.json](postman/urumi-gateway.postman_collection.json)
walks through every feature in order. Each request checks its expected status code and saves
tokens, ids and keys into collection variables, so you never copy them by hand.

## 1. Start everything

```bash
# terminal 1: the WooCommerce MCP server (store must be up at http://wordpress.local)
cd services/woocommerce-mcp && poetry run woocommerce-mcp        # http://127.0.0.1:8000/mcp

# terminal 2: the gateway
cd services/gateway
poetry install
cp .env.example .env    # set DB_URL (its own database) and AUTH_TOKEN_PEPPER (>= 32 chars)
poetry run alembic upgrade head
poetry run gateway      # http://127.0.0.1:8100
```

Check `GET http://127.0.0.1:8100/health/ready` returns `{"status": "ready"}`.

## 2. Import and configure

1. Postman → **Import** → choose the collection file.
2. Open the collection → **Variables**. Defaults work locally; change `password` if you like
   (at least 12 characters). `mcpUrl` is where the gateway reaches the MCP server.

## 3. Run the folders in order

You can click through request by request, or use **Run collection** (runner) for the whole set.

| Folder | What it shows |
| --- | --- |
| 0. Health | liveness, readiness (DB migrated), Prometheus metrics |
| 1. Admin account | register + login; the session token (`ugs_…`) is saved as `adminToken` |
| 2. Connector registry | register the MCP server by slug, SSRF guard rejects a metadata URL, probe lists its 5 tools |
| 3. Team | create team (you become admin), team settings, enable the connector with a tool policy, catalog |
| 4. Invite a member | invitation token shown once, invitation-based registration, single use |
| 5. Members and grants | member sees nothing until granted `list_products` + `get_order` |
| 6. API keys | member and admin keys (`urk_…`, shown once), listing visibility |
| 7. Tools over REST | discovery is filtered per user, calls return MCP `CallToolResult`, ungranted tool → 403 |
| 8. Tools over MCP | raw JSON-RPC to `/v1/mcp/woocommerce`: `initialize`, `tools/list`, `tools/call` |
| 9. Authorization checks | member cannot list members, self-promote, smuggle `role`, use keys for management, reach another team, or demote the last admin |
| 10. Rotation and revocation | rotate with grace, owner-only rotation, revoked key → 401 |
| 11. Audit and usage | team audit log, member-only views, invocation list, summaries by tool/user, platform audit |
| 12. Cleanup | revoke an invitation, remove the member (their keys are revoked), logout |

**One manual step.** Right after *Register admin*, make that user a platform admin (registering
connectors is an operator action that the API never grants):

```bash
cd services/gateway && poetry run gateway-admin grant-platform-admin admin@example.com
```

*Create draft product* in folder 7 really creates a **draft** product in your store. Send it twice:
the second call returns the same product because of the `idempotency_key`.

To run the collection again from scratch, change `adminEmail`/`memberEmail` (accounts and slugs
are unique) or recreate the gateway database. If you rerun folder 2 against the same database,
*Register woocommerce connector* returns 409; set `connectorId` from *List connectors*.

## Hosted servers and connector credentials

These need the gateway running in the cluster (`{{baseUrl}}` = `http://gateway.urumi.local`), and
the `adminToken` of a platform admin. Add them as requests in a new folder:

| Request | Body / note |
| --- | --- |
| `GET /v1/hosted-servers/settings` | allowed images, limits, namespace |
| `POST /v1/hosted-servers` | the `web-search` or `github` body from [docs/hosted-mcp.md](../../docs/hosted-mcp.md); returns `status.phase: deploying` |
| `GET /v1/hosted-servers` | repeat until `status.phase` is `ready` |
| `POST /v1/connectors/{{id}}/probe` | lists the tools; GitHub without a token answers `upstream_unauthorized` |
| `PATCH /v1/connectors/{{id}}` | `{"credential": {"type": "bearer", "value": "<token>"}}` sets it; the response shows only `credential.header_name` |
| `PATCH /v1/connectors/{{id}}` | `{"enabled": false}` scales the pod to 0 and hides its tools |
| `PUT /v1/teams/{{teamId}}/connectors/web-search` | `{"allow_all_tools": true}`, then call `search` like any tool |
| `DELETE /v1/connectors/{{id}}` | 204; `kubectl -n urumi-mcp get all` shows the objects are gone |

## Calling the MCP endpoint yourself

`/v1/mcp/{connector}` is a stateless MCP Streamable HTTP server. Raw requests need:

```
POST http://127.0.0.1:8100/v1/mcp/woocommerce
Authorization: Bearer urk_…
Content-Type: application/json
Accept: application/json, text/event-stream
X-Request-ID: any-id-you-like            (optional; shows up in both services' logs)

{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_products","arguments":{"per_page":3}}}
```

Postman's own **MCP request** type (New → MCP, transport HTTP) should also work: URL
`http://127.0.0.1:8100/v1/mcp/woocommerce`, header `Authorization: Bearer <memberKey or adminKey>`.
The MCP Inspector works the same way (`npx @modelcontextprotocol/inspector`, transport *Streamable HTTP*,
same URL and header).

## Reading the results

* Gateway errors: HTTP status + `{"error": {"code", "message", "request_id"}}`.
* Tool errors from the store: HTTP 200 with `"isError": true` and text like
  `not_found: Order 1 was not found.` That is the upstream tool answering, not a gateway failure.
* `upstream_unavailable` (502): the MCP server is not running or `mcpUrl` is wrong. On macOS keep
  `WC_IP_FAMILY=ipv4` in the MCP server's `.env` for `*.local` store names.
* `rate_limited` (429): wait for `Retry-After` seconds (logins are limited to 10/min per email).
* Database view: `audit_events` and `tool_invocations` in the gateway database hold every
  action and call; neither stores tokens, keys, passwords or tool arguments.
