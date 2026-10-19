import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query"

import { gateway } from "@/lib/api/gateway"
import type {
  DeployHostedServerRequest,
  McpTool,
  RegisterConnectorRequest,
  TeamConnector,
  ToolSet,
  UpdateConnectorRequest,
  UpdateHostedServerRequest,
} from "@/lib/api/types"
import { sessionToken } from "@/lib/auth/store"
import { qk } from "@/lib/query"

// --- platform registry --------------------------------------------------------------------------

export function useRegistry() {
  return useQuery({
    queryKey: qk.registry,
    queryFn: () => gateway.connectors.list(sessionToken()),
  })
}

export function useRegisterConnector() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: RegisterConnectorRequest) =>
      gateway.connectors.register(sessionToken(), body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.registry }),
  })
}

export function useUpdateConnector() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdateConnectorRequest }) =>
      gateway.connectors.update(sessionToken(), id, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: qk.registry })
      void queryClient.invalidateQueries({ queryKey: qk.hosted })
      void queryClient.invalidateQueries({ queryKey: ["teams"] })
    },
  })
}

export function useRemoveConnector() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => gateway.connectors.remove(sessionToken(), id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: qk.registry })
      void queryClient.invalidateQueries({ queryKey: qk.hosted })
      void queryClient.invalidateQueries({ queryKey: ["teams"] })
    },
  })
}

export function useProbeConnector() {
  return useMutation({
    mutationFn: (id: string) => gateway.connectors.probe(sessionToken(), id),
  })
}

// --- hosted MCP servers (platform admins) --------------------------------------------------------

export function useHostedSettings(enabled: boolean) {
  return useQuery({
    queryKey: qk.hostedSettings,
    queryFn: () => gateway.hosted.settings(sessionToken()),
    enabled,
    staleTime: 5 * 60_000,
  })
}

/** Hosted servers with live cluster status; polls while any of them is still deploying. */
export function useHostedServers(enabled: boolean) {
  return useQuery({
    queryKey: qk.hosted,
    queryFn: () => gateway.hosted.list(sessionToken()),
    enabled,
    refetchInterval: (q) =>
      q.state.data?.some((h) => h.status.phase === "deploying") ? 3000 : false,
  })
}

function useInvalidateHosted() {
  const queryClient = useQueryClient()
  return () => {
    void queryClient.invalidateQueries({ queryKey: qk.hosted })
    void queryClient.invalidateQueries({ queryKey: qk.registry })
    void queryClient.invalidateQueries({ queryKey: ["teams"] })
  }
}

export function useDeployHosted() {
  const invalidate = useInvalidateHosted()
  return useMutation({
    mutationFn: (body: DeployHostedServerRequest) => gateway.hosted.deploy(sessionToken(), body),
    onSuccess: invalidate,
  })
}

export function useUpdateHosted() {
  const invalidate = useInvalidateHosted()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdateHostedServerRequest }) =>
      gateway.hosted.update(sessionToken(), id, body),
    onSuccess: invalidate,
  })
}

export function useRestartHosted() {
  const invalidate = useInvalidateHosted()
  return useMutation({
    mutationFn: (id: string) => gateway.hosted.restart(sessionToken(), id),
    onSuccess: invalidate,
  })
}

// --- team policy, discovery and grants ------------------------------------------------------------

export function useTeamConnectors(teamId: string) {
  return useQuery({
    queryKey: qk.teamConnectors(teamId),
    queryFn: () => gateway.connectors.team(sessionToken(), teamId),
  })
}

export function useConnectorTools(teamId: string, slug: string, enabled = true) {
  return useQuery({
    queryKey: qk.tools(teamId, slug),
    queryFn: () => gateway.connectors.tools(sessionToken(), teamId, slug),
    enabled,
    retry: false,
  })
}

export interface DiscoveredTool extends McpTool {
  connector: TeamConnector
}

/** Every tool the caller may use, across the team's connectors. A connector whose upstream is
 *  down is reported in `failed` instead of failing the whole catalog. */
export function useAllTools(teamId: string) {
  const connectors = useTeamConnectors(teamId)
  const usable = (connectors.data ?? []).filter(
    (c) => c.your_access.allow_all_tools || c.your_access.tools.length > 0
  )
  const results = useQueries({
    queries: usable.map((c) => ({
      queryKey: qk.tools(teamId, c.slug),
      queryFn: () => gateway.connectors.tools(sessionToken(), teamId, c.slug),
      retry: false,
    })),
  })
  const tools: DiscoveredTool[] = []
  const failed: Array<{ connector: TeamConnector; error: unknown }> = []
  results.forEach((r, i) => {
    if (r.data) tools.push(...r.data.map((t) => ({ ...t, connector: usable[i] })))
    if (r.error) failed.push({ connector: usable[i], error: r.error })
  })
  return {
    connectors,
    tools,
    failed,
    isLoading: connectors.isLoading || results.some((r) => r.isLoading),
    refetch: () => {
      void connectors.refetch()
      results.forEach((r) => void r.refetch())
    },
  }
}

export function useCatalog(teamId: string, slug: string, enabled: boolean) {
  return useQuery({
    queryKey: qk.catalog(teamId, slug),
    queryFn: () => gateway.connectors.catalog(sessionToken(), teamId, slug),
    enabled,
    retry: false,
  })
}

export function useSetTeamPolicy(teamId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ slug, body }: { slug: string; body: ToolSet }) =>
      gateway.connectors.setTeamPolicy(sessionToken(), teamId, slug, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.teamConnectors(teamId) }),
  })
}

export function useRemoveTeamPolicy(teamId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (slug: string) => gateway.connectors.removeTeamPolicy(sessionToken(), teamId, slug),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.teamConnectors(teamId) }),
  })
}

export function useGrants(teamId: string, userId: string | null) {
  return useQuery({
    queryKey: qk.grants(teamId, userId ?? ""),
    queryFn: () => gateway.grants.list(sessionToken(), teamId, userId!),
    enabled: Boolean(userId),
  })
}

export function useSetGrant(teamId: string, userId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ slug, body }: { slug: string; body: ToolSet }) =>
      gateway.grants.set(sessionToken(), teamId, userId, slug, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.grants(teamId, userId) }),
  })
}

export function useRevokeGrant(teamId: string, userId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (slug: string) => gateway.grants.revoke(sessionToken(), teamId, userId, slug),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.grants(teamId, userId) }),
  })
}
