import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { gateway } from "@/lib/api/gateway"
import type { TeamRole, UpdateTeamRequest } from "@/lib/api/types"
import { sessionToken, useAuthStore } from "@/lib/auth/store"
import { qk } from "@/lib/query"

export function useTeam(teamId: string | null) {
  return useQuery({
    queryKey: qk.team(teamId ?? ""),
    queryFn: () => gateway.teams.get(sessionToken(), teamId!),
    enabled: Boolean(teamId),
  })
}

export function useMembers(teamId: string, enabled = true) {
  return useQuery({
    queryKey: qk.members(teamId),
    queryFn: () => gateway.teams.members(sessionToken(), teamId),
    enabled,
  })
}

export function useInvitations(teamId: string, includeClosed: boolean) {
  return useQuery({
    queryKey: qk.invitations(teamId, includeClosed),
    queryFn: () => gateway.invitations.list(sessionToken(), teamId, includeClosed),
  })
}

export function useCreateTeam() {
  const queryClient = useQueryClient()
  const setActiveTeam = useAuthStore((s) => s.setActiveTeam)
  return useMutation({
    mutationFn: (name: string) => gateway.teams.create(sessionToken(), name),
    onSuccess: async (team) => {
      setActiveTeam(team.id)
      await queryClient.invalidateQueries({ queryKey: qk.me })
      await queryClient.invalidateQueries({ queryKey: qk.teams })
    },
  })
}

export function useUpdateTeam(teamId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: UpdateTeamRequest) => gateway.teams.update(sessionToken(), teamId, body),
    onSuccess: (team) => {
      queryClient.setQueryData(qk.team(teamId), team)
      void queryClient.invalidateQueries({ queryKey: qk.me })
    },
  })
}

export function useChangeRole(teamId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: TeamRole }) =>
      gateway.teams.changeRole(sessionToken(), teamId, userId, role),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: qk.members(teamId) })
      void queryClient.invalidateQueries({ queryKey: qk.me })
    },
  })
}

export function useRemoveMember(teamId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (userId: string) => gateway.teams.removeMember(sessionToken(), teamId, userId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.members(teamId) }),
  })
}

export function useCreateInvitation(teamId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ email, role }: { email: string; role: TeamRole }) =>
      gateway.invitations.create(sessionToken(), teamId, email, role),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["teams", teamId, "invitations"] }),
  })
}

export function useRevokeInvitation(teamId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (invitationId: string) =>
      gateway.invitations.revoke(sessionToken(), teamId, invitationId),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["teams", teamId, "invitations"] }),
  })
}

export function useAcceptInvitation() {
  const queryClient = useQueryClient()
  const setActiveTeam = useAuthStore((s) => s.setActiveTeam)
  return useMutation({
    mutationFn: (token: string) => gateway.invitations.accept(sessionToken(), token),
    onSuccess: async (invitation) => {
      setActiveTeam(invitation.team_id)
      await queryClient.invalidateQueries({ queryKey: qk.me })
    },
  })
}
