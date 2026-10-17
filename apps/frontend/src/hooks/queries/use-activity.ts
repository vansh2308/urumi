import { keepPreviousData, useQuery } from "@tanstack/react-query"

import { agent } from "@/lib/api/agent"
import { gateway, type AuditQuery, type InvocationQuery, type SummaryQuery } from "@/lib/api/gateway"
import { sessionToken } from "@/lib/auth/store"
import { qk } from "@/lib/query"

export function useTeamAudit(teamId: string, q: AuditQuery, enabled = true) {
  return useQuery({
    queryKey: qk.teamAudit(teamId, q),
    queryFn: () => gateway.activity.teamAudit(sessionToken(), teamId, q),
    placeholderData: keepPreviousData,
    enabled,
  })
}

export function usePlatformAudit(q: AuditQuery, enabled: boolean) {
  return useQuery({
    queryKey: qk.platformAudit(q),
    queryFn: () => gateway.activity.platformAudit(sessionToken(), q),
    placeholderData: keepPreviousData,
    enabled,
  })
}

export function useInvocations(teamId: string, q: InvocationQuery) {
  return useQuery({
    queryKey: qk.invocations(teamId, q),
    queryFn: () => gateway.activity.invocations(sessionToken(), teamId, q),
    placeholderData: keepPreviousData,
  })
}

export function useUsageSummary(teamId: string, q: SummaryQuery) {
  return useQuery({
    queryKey: qk.usageSummary(teamId, q),
    queryFn: () => gateway.activity.summary(sessionToken(), teamId, q),
    placeholderData: keepPreviousData,
  })
}

export function useServiceHealth() {
  return useQuery({
    queryKey: qk.health,
    queryFn: async () => {
      const settle = async (fn: () => Promise<{ status: string }>) => {
        try {
          return (await fn()).status === "ready" ? "ready" : "not_ready"
        } catch {
          return "unreachable"
        }
      }
      const [gw, ag] = await Promise.all([
        settle(gateway.health.ready),
        settle(agent.health.ready),
      ])
      return { gateway: gw, agent: ag } as const
    },
    refetchInterval: 30_000,
    retry: false,
  })
}
