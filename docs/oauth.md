# OAuth for MCP clients

MCP clients can connect to the gateway with nothing but its URL: the client discovers the
gateway's authorization server, registers itself, sends the user to the dashboard to approve,
and receives short-lived tokens. API keys keep working for scripts and for clients without
OAuth support.

This follows the [MCP authorization spec](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization):
OAuth 2.1 with PKCE, Authorization Server Metadata (RFC 8414), Dynamic Client Registration
(RFC 7591), Protected Resource Metadata (RFC 9728), resource indicators (RFC 8707) and token
revocation (RFC 7009).

## Flow

```
MCP client                      gateway (gateway.urumi.local)                 dashboard (urumi.local)
    │ POST /v1/mcp (no token) ──▶ 401  WWW-Authenticate: Bearer resource_metadata=".../.well-known/oauth-protected-resource/v1/mcp"
    │ GET  /.well-known/oauth-protected-resource/v1/mcp ──▶ {resource, authorization_servers}
    │ GET  /.well-known/oauth-authorization-server     ──▶ {authorize, token, register, revoke, S256}
    │ POST /register ──▶ client_id (secret only for confidential clients)
    │ browser: GET /authorize?…&code_challenge=…  ──▶ 302 ──────────────────────▶ /oauth/consent?request=uoq_…
    │                                                                  user signs in, picks a team, Approve
    │ ◀──────────────── 302 redirect_uri?code=uoc_…&state=…&iss=… ◀───────────────────────┘
    │ POST /token (code + code_verifier) ──▶ access_token (urk_…, 1 h) + refresh_token (uor_…)
    │ POST /v1/mcp  Authorization: Bearer urk_… ──▶ tools of every enabled server
    │ POST /token (refresh_token) ──▶ new pair; the old refresh token and access token stop working
```

## Design

* **Library, not hand-written protocol**: metadata, `/authorize`, `/token`, `/register`,
  `/revoke`, PKCE verification, redirect URI matching, client authentication and the RFC 9728
  document are the official MCP Python SDK's handlers (`mcp.server.auth`). The gateway supplies
  the storage and policy as the SDK's `OAuthAuthorizationServerProvider`
  (`services/gateway/src/gateway/oauth/service.py`) and assembles the routes
  (`gateway/oauth/routes.py`), adding per-IP rate limits and, for local clusters only, a
  plain-HTTP issuer.
* **Access tokens are platform API keys.** A token is an `urk_` key owned by the approving user,
  bound to the team chosen on the consent page, with the approved scopes and a one-hour expiry.
  Membership, team tool policy, member grants, scopes, rate limits, audit attribution and
  revocation therefore apply exactly as for any key; nothing in the tool path knows about OAuth.
  Each OAuth key belongs to a grant (`oauth_grants`: client + user + team + scopes) and is
  replaced on every refresh.
* **Consent lives in the dashboard**, which already has the user's session: `/authorize` stores
  the request and redirects to `/oauth/consent?request=uoq_…`; the page calls
  `POST /v1/oauth/requests/{id}/decision` with the session token and sends the browser back to
  the client. The page shows the client's self-declared name, the scopes, and the host the browser
  returns to, and warns that names are not verified.
* **What is stored**: request ids, codes and refresh tokens only as peppered HMACs; codes are
  single use and expire after 5 minutes; requests after 10; refresh tokens after 30 days and are
  rotated on every use (reusing one fails). Confidential clients' secrets are encrypted with
  `UPSTREAM_CREDENTIAL_KEYS` (the SDK compares secrets in plain text, so they cannot be hashed);
  public clients (PKCE, `token_endpoint_auth_method: none`, the norm for MCP clients) have none.
* **Redirect URIs** must be HTTPS, an `http://localhost` / `127.0.0.1` address (native apps,
  RFC 8252), or an app scheme such as `cursor://`. Plain HTTP to any other host is refused at
  registration.
* **Membership is re-checked** on every token and refresh exchange: removing someone from the
  team ends their OAuth access at the next refresh, and immediately for the current token because
  removing a member revokes their keys.

Scopes map one to one onto key scopes: `tools:read`, `tools:call` (default), `usage:read`.

## Endpoints

| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `GET /.well-known/oauth-protected-resource/v1/mcp` | none | RFC 9728: the resource and its authorization server |
| `GET /.well-known/oauth-authorization-server` | none | RFC 8414 metadata |
| `POST /register` | none (rate limited) | RFC 7591 dynamic client registration |
| `GET /authorize` | none (rate limited) | starts the flow, redirects to the consent page |
| `POST /token` | client auth or PKCE (rate limited) | code and refresh token exchange |
| `POST /revoke` | client auth | RFC 7009; revokes the whole grant (send `client_secret=""` for public clients, an SDK quirk) |
| `GET /v1/oauth/config` | none | `{enabled, issuer, mcp_url}` for the dashboard |
| `GET /v1/oauth/requests/{id}`, `POST …/{id}/decision` | session | consent page |
| `GET /v1/oauth/grants`, `DELETE /v1/oauth/grants/{id}` | session | Profile → Connected apps |

## Configuration

| Variable | Local (k3d) | Production |
| --- | --- | --- |
| `OAUTH_ENABLED` | `true` | `true` |
| `OAUTH_ISSUER_URL` | `http://gateway.urumi.local` | `https://gateway.<domain>` |
| `OAUTH_CONSENT_URL` | `http://urumi.local/oauth/consent` | `https://app.<domain>/oauth/consent` |
| `OAUTH_ALLOW_HTTP_ISSUER` | `true` (no TLS locally) | `false` |
| `OAUTH_ACCESS_TOKEN_TTL_SECONDS` / `OAUTH_REFRESH_TOKEN_TTL_DAYS` | 3600 / 30 | same |
| `OAUTH_REQUESTS_PER_MINUTE` | 30 per IP | same |

The issuer must be the gateway's public origin exactly as clients reach it: the protected
resource is `<issuer>/v1/mcp`. Running the gateway on the host: `OAUTH_ISSUER_URL=http://localhost:8100`
and `OAUTH_CONSENT_URL=http://localhost:5173/oauth/consent` (HTTP is allowed for localhost).

## Connect a client

**Claude Code**

```bash
claude mcp add --transport http urumi http://gateway.urumi.local/v1/mcp
# in Claude Code: /mcp → urumi → Authenticate; approve in the browser
```

**Cursor** (`~/.cursor/mcp.json`): `{"mcpServers": {"urumi": {"url": "http://gateway.urumi.local/v1/mcp"}}}`

**MCP Inspector**: `npx @modelcontextprotocol/inspector`, transport Streamable HTTP, URL
`http://gateway.urumi.local/v1/mcp`, then *Open Auth Settings → Quick OAuth Flow*.

Some clients only accept OAuth over HTTPS for non-localhost hosts. Locally, either use the
production-like TLS setup, or run the gateway on the host with the localhost issuer above. In
production the gateway is on HTTPS and this does not arise.

The dashboard shows the right URL and snippets under API keys → *Connect an MCP client*.
Connected clients are listed under Profile → *Connected apps*, and their current tokens appear
on the API keys page as "OAuth app" keys; disconnecting or revoking either ends access at once.

## Verified

* `services/gateway/tests/test_oauth.py`: discovery and the 401 challenge, registration rules,
  consent approve and deny, PKCE failure, single-use codes, member permissions on the issued
  token, refresh rotation and reuse rejection, revocation.
* Against the k3d cluster, the official MCP SDK client (`mcp.client.auth.OAuthClientProvider`)
  discovered the gateway from a 401, registered, completed PKCE after approval, listed the tools
  of both servers and called `woocommerce__list_products`; the consent page was exercised in a
  browser through sign-in, approval and the redirect back with `code`, `state` and `iss`.
