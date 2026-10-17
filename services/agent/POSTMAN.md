# Testing the agent in Postman

The collection [postman/urumi-agent.postman_collection.json](postman/urumi-agent.postman_collection.json)
walks through the agent in order. Requests check their status codes and save the conversation
id for the next ones.

## 1. Start everything

```bash
# terminal 1: the WooCommerce MCP server
cd services/woocommerce-mcp && poetry run woocommerce-mcp        # http://127.0.0.1:8000/mcp

# terminal 2: the gateway
cd services/gateway && poetry run gateway                       # http://127.0.0.1:8100

# terminal 3: the agent
cd services/agent
poetry install
cp .env.example .env    # DB_URL (its own database), GATEWAY_URL, GEMINI_API_KEY
poetry run alembic upgrade head
poetry run agent        # http://127.0.0.1:8200
```

Check `GET http://127.0.0.1:8200/health/ready` returns `{"status": "ready"}`.

## 2. Get two gateway API keys

The agent uses the gateway's platform API keys; it has no accounts of its own. The easiest way
is the gateway collection ([../gateway/POSTMAN.md](../gateway/POSTMAN.md)): run its folders
**1 to 6**. That gives you a team with the `woocommerce` connector, an admin with every tool, a
member granted only `list_products` and `get_order`, and one key for each, saved in the gateway
collection's variables as `adminKey` and `memberKey`. (Don't run the gateway's cleanup folders
first: they revoke the member's keys.)

Any key works as long as it has the `tools:read` and `tools:call` scopes (the default).

## 3. Import and configure

1. Postman → **Import** → choose the collection file.
2. Collection → **Variables**: paste the admin's key into `apiKey` and the member's into
   `memberApiKey`. `agentUrl` defaults to `http://127.0.0.1:8200`.

## 4. Run the folders in order

| Folder | What it shows |
| --- | --- |
| 0. Health | liveness, readiness (DB migrated), Prometheus metrics |
| 1. Identity and tool discovery | the key verified by the gateway; **available MCP tools** for the admin and the (smaller) set for the member |
| 2. Chat | **tool selection, gateway HTTP response and final answer** as JSON; a follow-up that uses context; a **streamed** run with several sequential tool calls; an ungranted action; a prompt-injection attempt; input validation |
| 3. Conversations and runs | messages with tool calls and results, run metrics (latency, first token, tokens, tool call log), another user gets 404 |
| 4. Usage metrics | your summary and the team's (admin only) |
| 5. Cleanup | delete the conversation |

The chat requests talk to a real model, so wording differs between runs. Tests only check
structure (a tool was called, it went through the gateway, there is an answer).

## Where to look in a response

**`GET /v1/tools`** is the tool retrieval: every tool the gateway returned for this key, the
function name the model sees (`woocommerce__list_products`), and its JSON input schema.

**`POST /v1/chat` with `"stream": false`** returns the whole run:

```jsonc
{
  "status": "completed",
  "tools_available": ["woocommerce__list_products", "..."],     // discovery
  "model_calls": [                                                // each model step
    {"step": 1, "finish_reason": "tool_calls", "tool_calls": ["woocommerce__list_products"], "latency_ms": 900},
    {"step": 2, "finish_reason": "stop", "tool_calls": [], "latency_ms": 1100}
  ],
  "tool_calls": [{                                                // tool selection and result
    "name": "woocommerce__list_products", "connector": "woocommerce", "tool": "list_products",
    "arguments": {"per_page": 3},
    "http_status": 200, "outcome": "success", "latency_ms": 640,  // gateway HTTP response
    "result": {"ok": true, "result": {"items": ["..."]}}          // exactly what the model saw
  }],
  "message": "Here are the 3 newest products ...",                // final answer
  "usage": {"input_tokens": 2100, "output_tokens": 180},
  "latency_ms": 2900, "first_token_ms": 2500
}
```

**`POST /v1/chat` with `"stream": true`** (the default): Postman lists the server-sent events as
they arrive: `run.started` → `tools.discovered` → `llm.request` → `llm.response` → `tool.call`
(tool and arguments) → `tool.result` (gateway HTTP status, outcome, result) → … →
`message.delta` (answer text as it streams) → `message.completed` → `run.completed` (status,
tokens, latency). With curl:

```bash
curl -N http://127.0.0.1:8200/v1/chat -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' -d '{"message": "List 3 products"}'
```

**`GET /v1/conversations/{id}/runs`** shows what was persisted per run: status, `latency_ms`,
`first_token_ms`, `llm_latency_ms`, `tool_latency_ms`, tokens and a `tool_call_log` with each
call's outcome and gateway HTTP status.

## Things worth trying by hand

* **Permission changes apply immediately.** As the admin, revoke the member's grant in the
  gateway (`DELETE …/members/{user_id}/grants/woocommerce`), then call `GET /v1/tools` with the
  member key: the list is empty and the model gets no tools.
* **Revoked key.** Revoke a key in the gateway; the next agent request answers `401`.
* **Audit correlation.** Send a chat with header `X-Request-ID: my-test-123`. The run's
  `request_id`, the agent's logs, and the gateway's audit and usage rows
  (`GET /v1/teams/{team}/usage/invocations` on the gateway) all carry `my-test-123`.
* **Busy conversation.** Send two streaming messages to the same `conversation_id` at once; the
  second gets `409 conflict`.
* **Rate limit.** More than `RUN_RATE_LIMIT_PER_MINUTE` messages a minute answer `429` with
  `Retry-After`.
* **Limits.** Restart with `RUN_MAX_TOOL_CALLS=1` and ask for something that needs two calls: the
  run ends with status `tool_limit` and a fixed message saying it stopped early.
* **Provider failure.** Restart with a wrong `GEMINI_API_KEY`: the run reports
  `error.code = llm_rejected` (`502` in JSON mode) and nothing is claimed as done.
