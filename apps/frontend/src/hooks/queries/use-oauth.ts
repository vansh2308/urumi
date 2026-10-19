import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { gateway } from "@/lib/api/gateway"
import { sessionToken } from "@/lib/auth/store"
import { qk } from "@/lib/query"

/** Whether MCP clients can sign in with OAuth, and the MCP URL to give them. Public. */
export function useOAuthConfig() {
  return useQuery({ queryKey: qk.oauthConfig, queryFn: () => gateway.oauth.config(), staleTime: 5 * 60_000 })
}

export function useConsentRequest(id: string) {
  return useQuery({
    queryKey: qk.oauthRequest(id),
    queryFn: () => gateway.oauth.request(sessionToken(), id),
    enabled: Boolean(id),
    retry: false,
  })
}

export function useConsentDecision(id: string) {
  return useMutation({
    mutationFn: (body: { approve: boolean; team_id?: string }) =>
      gateway.oauth.decide(sessionToken(), id, body),
  })
}

/** Apps the user connected with OAuth, in all their teams. */
export function useOAuthGrants() {
  return useQuery({ queryKey: qk.oauthGrants, queryFn: () => gateway.oauth.grants(sessionToken()) })
}

export function useRevokeOAuthGrant() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => gateway.oauth.revokeGrant(sessionToken(), id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: qk.oauthGrants })
      void queryClient.invalidateQueries({ queryKey: ["teams"] })
    },
  })
}
