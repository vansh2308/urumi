import { QueryClient } from "@tanstack/react-query"

import { ApiError } from "@/lib/api/http"

// Client errors (4xx) are answers, not glitches: retrying them only repeats the refusal.
function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 429) {
    return false
  }
  return failureCount < 2
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: shouldRetry,
      refetchOnWindowFocus: false,
    },
    mutations: { retry: false },
  },
})

export const qk = {
  me: ["me"] as const,
  health: ["health"] as const,
  teams: ["teams"] as const,
  team: (teamId: string) => ["teams", teamId] as const,
  members: (teamId: string) => ["teams", teamId, "members"] as const,
  invitations: (teamId: string, includeClosed: boolean) =>
    ["teams", teamId, "invitations", { includeClosed }] as const,
  apiKeys: (teamId: string, includeInactive: boolean) =>
    ["teams", teamId, "api-keys", { includeInactive }] as const,
  registry: ["connectors"] as const,
  connector: (id: string) => ["connectors", id] as const,
  teamConnectors: (teamId: string) => ["teams", teamId, "connectors"] as const,
  catalog: (teamId: string, slug: string) => ["teams", teamId, "connectors", slug, "catalog"] as const,
  tools: (teamId: string, slug: string) => ["teams", teamId, "connectors", slug, "tools"] as const,
  grants: (teamId: string, userId: string) => ["teams", teamId, "grants", userId] as const,
  teamAudit: (teamId: string, q: object) => ["teams", teamId, "audit", q] as const,
  platformAudit: (q: object) => ["platform-audit", q] as const,
  invocations: (teamId: string, q: object) => ["teams", teamId, "invocations", q] as const,
  usageSummary: (teamId: string, q: object) => ["teams", teamId, "usage-summary", q] as const,
  agent: {
    me: (keyId: string) => ["agent", keyId, "me"] as const,
    tools: (keyId: string) => ["agent", keyId, "tools"] as const,
    conversations: (keyId: string) => ["agent", keyId, "conversations"] as const,
    conversation: (keyId: string, id: string) => ["agent", keyId, "conversations", id] as const,
    runs: (keyId: string, id: string) => ["agent", keyId, "conversations", id, "runs"] as const,
    usage: (keyId: string, scope: string, days: number) =>
      ["agent", keyId, "usage", scope, days] as const,
  },
}
