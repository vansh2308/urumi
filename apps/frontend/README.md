# Urumi dashboard

React dashboard for the Urumi MCP gateway and AI agent: overview, agent chat, MCP servers,
tools, usage, audit logs, agent metrics, team, API keys, settings and profile.

Stack: Vite, React 19, TypeScript, Tailwind CSS v4, shadcn/ui (Mist), TanStack Query and Table,
React Router, React Hook Form + Zod, Recharts (shadcn charts), Zustand (live chat run only).

## Run locally

Start the three services first (see `services/*/README.md`):

```bash
cd services/woocommerce-mcp && poetry run woocommerce-mcp   # :8000
cd services/gateway && poetry run gateway                    # :8100
cd services/agent && poetry run agent                        # :8201
```

Then the dashboard:

```bash
cd apps/frontend
npm install
npm run dev        # http://localhost:5173
```

The dev server proxies `/api/gateway` → `127.0.0.1:8100` and `/api/agent` → `127.0.0.1:8201`
(override with `GATEWAY_PROXY_TARGET` / `AGENT_PROXY_TARGET`, see `.env.example`).

`npm run build` type-checks and writes a static bundle to `dist/`. When serving it from a
different origin than the services, set `VITE_GATEWAY_URL` / `VITE_AGENT_URL` at build time and
add the dashboard origin to `GATEWAY_CORS_ALLOWED_ORIGINS` and `AGENT_CORS_ALLOWED_ORIGINS`.

## Authentication model

- **Sign-in** uses the gateway's `/v1/auth/login` session token (`ugs_`). It authorizes every
  management call; roles come from `/v1/me` and are enforced by the gateway on each request.
  The UI only hides what the backend would refuse.
- **Chat and tool runs** need a platform API key (`urk_`): the gateway only lets API keys invoke
  tools and the agent authenticates with the caller's key. The dashboard asks you to create a
  1-day key (or paste one) and keeps it in the tab's `sessionStorage`; it is dropped on sign-out,
  when the tab closes, or when a service rejects it.
- Registering MCP servers and the platform audit log need a platform admin:
  `cd services/gateway && poetry run gateway-admin grant-platform-admin <email>`.

## Layout

```
src/
  lib/api/         typed gateway and agent clients (SSE run stream in agent.ts)
  lib/auth/        session store
  hooks/queries/   TanStack Query hooks per domain
  components/      shadcn ui, layout shell, data table, shared bits
  features/        feature components (chat, connectors, tools, team, api-keys, ...)
  pages/           one file per route
  routes/          router and guards
```
