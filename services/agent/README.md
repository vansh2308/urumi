# agent

A single LLM agent with native tool calling. It discovers the caller's tools through the
[gateway](../gateway), lets the model call them (through the gateway, as the caller) and streams
the run back. It contains no WooCommerce logic and no tool list: whatever connectors the team
enables in the gateway become the agent's tools.

```
client ──Bearer urk_…──▶ agent ──same urk_ key──▶ gateway ──MCP──▶ woocommerce-mcp ──▶ store
                           │
                           └──▶ Gemini (behind a provider interface)
```

For a step-by-step manual test, see [POSTMAN.md](POSTMAN.md) and the importable
[collection](postman/urumi-agent.postman_collection.json).

## Security model

* **The caller's own key, end to end.** Clients authenticate with a gateway platform API key
  (`urk_…`, scopes `tools:read` + `tools:call`). The agent verifies it with the gateway's
  `GET /v1/me` on every request and forwards it on every gateway call of that request. The agent
  has no gateway credential of its own, so it can never do more than the user could: the gateway
  applies team policy ∩ member grant ∩ key restrictions, rate limits, audits the call under the
  user's key and records usage. Session tokens (`ugs_…`) are refused because the gateway only
  lets API keys invoke tools.
* **No direct MCP access.** Tools are listed with `GET /v1/teams/{team}/connectors[/{slug}/tools]`
  and invoked with `POST /v1/teams/{team}/connectors/{slug}/tools/{tool}/call`. The agent never
  learns upstream URLs or WooCommerce credentials.
* **Secrets stay server-side.** `GEMINI_API_KEY` lives only in the agent's environment; it is
  never returned or logged. API keys are never stored or logged; logs never contain message
  text, tool arguments or tool results.
* **Model output cannot widen access.** The model sees only function names discovered for this
  caller in this run. A name it invents is answered with `unknown_tool`; arguments are validated
  against the tool's JSON Schema before anything is sent; the team, connector and credentials of
  a call come from the verified identity and the trusted name map, never from model output.
  Requests reject unknown fields, so clients cannot set the system prompt, model or identity.
* **Prompt injection.** The system instruction is application code. Tool results go back to the
  model as structured function responses, and the instruction tells the model to treat them as
  untrusted data. Whatever the model is talked into, the gateway still checks the user's
  permissions on every call.
* **No hallucinated success.** Every tool outcome reaches the model as `{"ok": true, "result"}` or
  `{"ok": false, "error": {code, message}}`; timeouts are marked `outcome_unknown` (a write may or
  may not have happened). Limit stops end with a fixed, honest message rather than model text.
* **Tenant isolation.** Conversations belong to `(team_id, user_id)` from the verified identity
  and every query filters on both; another user's conversation id answers 404.

## Agent loop

One run per chat request:

1. Discover tools for the caller (connectors in parallel; one failing connector is skipped and
   reported in `unavailable_connectors`).
2. Load the conversation's recent messages (`RUN_HISTORY_MAX_MESSAGES`), repairing any tool call
   interrupted in an earlier run.
3. Call the model with the tools. Stream its text. If it requests tools, execute them one after
   another through the gateway, append the results and call the model again.
4. Stop at a final answer, or at a limit: `RUN_MAX_STEPS` model responses, `RUN_MAX_TOOL_CALLS`
   tool calls, `RUN_MAX_PARALLEL_TOOL_CALLS` per response, `RUN_MAX_TOKENS` per run,
   `RUN_TIMEOUT_SECONDS` wall clock; `LLM_REQUEST_TIMEOUT_SECONDS` per model call and
   `GATEWAY_TOOL_CALL_TIMEOUT_SECONDS` per tool call.

A conversation is locked while a run is active (a second message gets `409`); runs are rate
limited per user (`RUN_RATE_LIMIT_PER_MINUTE`, `429` with `Retry-After`). Provider `429`/`5xx`
answers are retried with backoff only while nothing has been streamed yet.

## LLM providers

`agent.llm.base` defines provider-neutral messages, tool specs and responses and an
`LlmProvider` protocol (`generate(request, on_text) -> LlmResponse`). `agent.llm.gemini` is the
Gemini adapter (REST `streamGenerateContent` over SSE, JSON Schema tool parameters, thought
signatures round-tripped). To add a provider, implement the protocol and register it in
`agent.llm.factory`; the loop does not change.

## API

OpenAPI docs are served at `/docs`. Errors are always `{"error": {"code", "message",
"request_id", ...}}`. All `/v1` routes take `Authorization: Bearer urk_…`.

| Endpoint | Purpose |
| --- | --- |
| `GET /health/live`, `GET /health/ready`, `GET /metrics` | probes (ready = PostgreSQL migrated), Prometheus |
| `GET /v1/me` | the key's user, team and role as verified by the gateway |
| `GET /v1/tools` | tools offered to the model for this caller, with input schemas |
| `POST /v1/chat` | `{"message", "conversation_id"?, "stream"?}`; SSE events or one JSON trace |
| `GET /v1/conversations` | own conversations, newest first (`limit`, `before`) |
| `GET /v1/conversations/{id}` | messages, tool calls with arguments, tool results the model saw |
| `GET /v1/conversations/{id}/runs` | per run: status, latency, first token, tokens, tool call log |
| `DELETE /v1/conversations/{id}` | delete it with its messages, runs and logs |
| `GET /v1/usage/summary` | runs, latency (avg/p50/p95), first token, tokens, tool outcomes (`scope=me|team`, `days`) |

### Chat events (`stream: true`)

| Event | Data |
| --- | --- |
| `run.started` | `run_id`, `conversation_id`, provider, model, `request_id` |
| `tools.discovered` | tools offered (name, connector, tool), `unavailable_connectors` |
| `llm.request` | step, context size, tools offered |
| `message.delta` | answer text as it streams |
| `llm.response` | step, finish reason, requested tools, tokens, latency |
| `tool.call` | the tool the model chose and its arguments |
| `tool.result` | outcome, gateway HTTP status, error code, latency, result given to the model |
| `message.completed` | final answer |
| `error` | `code`, `message`, `retryable` when the run stopped on a failure |
| `run.completed` | status, steps, tool calls, tokens, latency, first token, LLM and tool time |

With `stream: false` the same information comes back as one body (`tools_available`,
`model_calls`, `tool_calls`, `message`, `usage`, `latency_ms`, `error`); a failed run answers
`502` (`504` on timeout) with that body.

Run statuses: `completed`, `step_limit`, `tool_limit`, `token_limit`, `timeout`, `failed`,
`cancelled` (client disconnected). Tool outcomes: `success`, `tool_error`, `invalid_arguments`,
`unknown_tool`, `denied`, `not_found`, `rate_limited`, `upstream_error`, `gateway_error`,
`unknown`, `malformed_result`, `skipped`.

## Persistence

PostgreSQL with Alembic (own version table `agent_alembic_version`, so it can share a database):

| Table | Holds |
| --- | --- |
| `conversations` | owner (`team_id`, `user_id`), title, run lock |
| `messages` | user and assistant text, tool calls with arguments, tool results as the model saw them (truncated to `RUN_MAX_TOOL_RESULT_CHARS`) |
| `agent_runs` | one row per chat request: status, error code, request id, provider/model, steps, tool calls, tokens, latency, time to first token, LLM and tool time |
| `tool_calls` | one row per tool call: connector, tool, outcome, error code, gateway HTTP status, latency (no arguments or results) |

`SCHEMA_REVISION` in `db/models.py` must be bumped with every migration, or `/health/ready` fails.

## Observability

* **Request ids**: `X-Request-ID` is accepted or generated, returned, stored on the run and
  forwarded to the gateway, so the gateway's audit and usage rows for a run share the agent's id.
* **Logs**: JSON lines (`LOG_FORMAT=json`) with `request_id`, run and conversation ids, outcomes,
  counts and latencies; never keys, prompts, answers, tool arguments or results.
* **Metrics** (`/metrics`): `agent_runs_total{status}`, `agent_run_duration_seconds`,
  `agent_first_token_seconds`, `agent_llm_requests_total{provider,model,outcome}`,
  `agent_llm_request_duration_seconds`, `agent_llm_tokens_total{direction}`,
  `agent_tool_calls_total{connector,tool,outcome}`, `agent_tool_call_duration_seconds`,
  `agent_gateway_failures_total`, `agent_rate_limited_total`, HTTP request counts and latency.

## Architecture

```
src/agent/
  __main__.py      process entry point (uvicorn, graceful shutdown)
  app.py           FastAPI app, routes, middleware, lifespan
  container.py     composition root (dependency injection, resource ownership)
  config.py        pydantic-settings, grouped by concern
  api/             routes, schemas, auth dependency, middleware, error handlers
  services/        runner (agent loop), toolbox (discovery), tool_executor, conversations,
                   usage, prompt
  gateway/         HTTP client for the gateway (identity, discovery, invocation)
  llm/             provider-neutral interface, Gemini adapter, provider factory
  db/              SQLAlchemy models and engine
alembic/           migrations
```

## Running locally

Requires Python 3.12+, Poetry 2, PostgreSQL, a running gateway with a connector enabled for
your team, and a Gemini API key.

```bash
cd services/agent
poetry install
cp .env.example .env              # set DB_URL (its own database), GATEWAY_URL, GEMINI_API_KEY
poetry run alembic upgrade head
poetry run agent                  # serves http://127.0.0.1:8201
```

## Deployment notes

* Run it next to the gateway and reach the gateway over the cluster network (`GATEWAY_URL`).
  The agent needs no gateway secret.
* Put `DB_URL` and `GEMINI_API_KEY` in a Secret. Expose `/metrics` only inside the cluster.
* Keep `AGENT_SHUTDOWN_GRACE_SECONDS` (and the pod's `terminationGracePeriodSeconds` above it)
  close to `RUN_TIMEOUT_SECONDS` so deploys don't cut runs short; runs still open at shutdown
  are cancelled and recorded as `cancelled`.
* Disable buffering for `text/event-stream` at the ingress (the agent sends
  `X-Accel-Buffering: no` and a keep-alive comment every 15 s).

## Development

```bash
poetry run ruff format .
poetry run ruff check .
poetry run mypy
```

## Tests

The suite never reaches the real database, gateway or model provider. PostgreSQL tests create a
throwaway database on a local server named by `TEST_DATABASE_URL`, migrate it and drop it
afterwards (skipped when it is unset; non-local hosts are refused). The gateway is an in-process
fake that enforces per-key tool access, and the model is a scripted provider behind the same
`LlmProvider` interface as Gemini.

```bash
docker run -d --rm --name urumi-test-postgres -e POSTGRES_PASSWORD=postgres \
  -p 127.0.0.1:55432:5432 --tmpfs /var/lib/postgresql/data postgres:16-alpine
TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55432/postgres poetry run pytest
```

What is covered: the tool-calling loop through the gateway with persistence and run metrics,
multi-turn context, argument validation and unknown tools (never forwarded), permission denial,
upstream and malformed results (never reported as success), step limits, provider failures, SSE
event order, conversation ownership, request refusal before a run starts, and the Gemini wire
format (request translation, streaming, tool-call parsing, retries, safe errors).
