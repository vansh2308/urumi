import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { gateway } from "@/lib/api/gateway"
import type { CreateApiKeyRequest } from "@/lib/api/types"
import { sessionToken } from "@/lib/auth/store"
import { qk } from "@/lib/query"

export function useApiKeys(teamId: string, includeInactive: boolean) {
  return useQuery({
    queryKey: qk.apiKeys(teamId, includeInactive),
    queryFn: () => gateway.apiKeys.list(sessionToken(), teamId, includeInactive),
  })
}

function useInvalidateKeys(teamId: string) {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: ["teams", teamId, "api-keys"] })
}

export function useCreateApiKey(teamId: string) {
  const invalidate = useInvalidateKeys(teamId)
  return useMutation({
    mutationFn: (body: CreateApiKeyRequest) => gateway.apiKeys.create(sessionToken(), teamId, body),
    onSuccess: invalidate,
  })
}

export function useRevokeApiKey(teamId: string) {
  const invalidate = useInvalidateKeys(teamId)
  return useMutation({
    mutationFn: (keyId: string) => gateway.apiKeys.revoke(sessionToken(), teamId, keyId),
    onSuccess: invalidate,
  })
}

export function useRotateApiKey(teamId: string) {
  const invalidate = useInvalidateKeys(teamId)
  return useMutation({
    mutationFn: ({ keyId, graceHours }: { keyId: string; graceHours: number }) =>
      gateway.apiKeys.rotate(sessionToken(), teamId, keyId, graceHours),
    onSuccess: invalidate,
  })
}
