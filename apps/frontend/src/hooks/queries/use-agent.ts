import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { agent } from "@/lib/api/agent"
import { ApiError } from "@/lib/api/http"
import type { ToolKey } from "@/lib/auth/store"
import { qk } from "@/lib/query"

// Agent queries are keyed by the API key's id: conversations belong to the key's user and team.
export const keyOf = (k: ToolKey | null) => k?.keyId ?? k?.prefix ?? "none"

/** Drops a rejected key (expired, revoked) so the UI asks for a new one. */
function onAuthError(error: unknown, clear: () => void) {
  if (error instanceof ApiError && error.status === 401) clear()
}

const PAGE = 30

export function useConversations(toolKey: ToolKey | null, clear: () => void) {
  return useInfiniteQuery({
    queryKey: qk.agent.conversations(keyOf(toolKey)),
    queryFn: async ({ pageParam }) => {
      try {
        return await agent.conversations.list(toolKey!.key, PAGE, pageParam)
      } catch (e) {
        onAuthError(e, clear)
        throw e
      }
    },
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => (last.length === PAGE ? last[last.length - 1].updated_at : undefined),
    enabled: Boolean(toolKey),
  })
}

export function useConversation(toolKey: ToolKey | null, id: string | undefined) {
  return useQuery({
    queryKey: qk.agent.conversation(keyOf(toolKey), id ?? ""),
    queryFn: () => agent.conversations.get(toolKey!.key, id!),
    enabled: Boolean(toolKey && id),
  })
}

export function useRuns(toolKey: ToolKey | null, id: string | undefined) {
  return useQuery({
    queryKey: qk.agent.runs(keyOf(toolKey), id ?? ""),
    queryFn: () => agent.conversations.runs(toolKey!.key, id!),
    enabled: Boolean(toolKey && id),
  })
}

export function useDeleteConversation(toolKey: ToolKey | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => agent.conversations.delete(toolKey!.key, id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.agent.conversations(keyOf(toolKey)) }),
  })
}

export function useAgentTools(toolKey: ToolKey | null) {
  return useQuery({
    queryKey: qk.agent.tools(keyOf(toolKey)),
    queryFn: () => agent.tools(toolKey!.key),
    enabled: Boolean(toolKey),
    staleTime: 60_000,
  })
}

export function useAgentUsage(toolKey: ToolKey | null, scope: "me" | "team", days: number) {
  return useQuery({
    queryKey: qk.agent.usage(keyOf(toolKey), scope, days),
    queryFn: () => agent.usage(toolKey!.key, scope, days),
    enabled: Boolean(toolKey),
  })
}
