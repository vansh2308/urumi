import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { gateway } from "@/lib/api/gateway"
import type { Store } from "@/lib/api/types"
import { sessionToken } from "@/lib/auth/store"
import { qk } from "@/lib/query"

/** Poll while anything is still moving; a settled list is not refetched in the background. */
const POLL_MS = 5_000
export const isBusy = (s: Pick<Store, "status">) =>
  s.status === "requested" || s.status === "provisioning" || s.status === "deleting"

export function useStoreSettings() {
  return useQuery({
    queryKey: qk.storeSettings,
    queryFn: () => gateway.stores.settings(sessionToken()),
    staleTime: 5 * 60_000,
  })
}

export function useStores(teamId: string, enabled = true) {
  return useQuery({
    queryKey: qk.stores(teamId),
    queryFn: () => gateway.stores.list(sessionToken(), teamId),
    enabled,
    refetchInterval: (query) => (query.state.data?.some(isBusy) ? POLL_MS : false),
  })
}

export function useStore(teamId: string, id: string) {
  return useQuery({
    queryKey: qk.store(teamId, id),
    queryFn: () => gateway.stores.get(sessionToken(), teamId, id),
    refetchInterval: (query) => (query.state.data && isBusy(query.state.data) ? POLL_MS : false),
  })
}

function useInvalidateStores(teamId: string) {
  const queryClient = useQueryClient()
  return () => {
    void queryClient.invalidateQueries({ queryKey: qk.stores(teamId) })
    // A ready or deleted store adds or removes an MCP server (and its tools).
    void queryClient.invalidateQueries({ queryKey: qk.registry })
    void queryClient.invalidateQueries({ queryKey: qk.teamConnectors(teamId) })
  }
}

export function useCreateStore(teamId: string) {
  const invalidate = useInvalidateStores(teamId)
  return useMutation({
    mutationFn: ({ name, idempotencyKey }: { name: string; idempotencyKey: string }) =>
      gateway.stores.create(sessionToken(), teamId, name, idempotencyKey),
    onSuccess: invalidate,
  })
}

export function useDeleteStore(teamId: string) {
  const invalidate = useInvalidateStores(teamId)
  return useMutation({
    mutationFn: (id: string) => gateway.stores.remove(sessionToken(), teamId, id),
    onSuccess: invalidate,
  })
}

export function useRetryStore(teamId: string) {
  const invalidate = useInvalidateStores(teamId)
  return useMutation({
    mutationFn: (id: string) => gateway.stores.retry(sessionToken(), teamId, id),
    onSuccess: invalidate,
  })
}
