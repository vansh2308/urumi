import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "react-router"

import { gateway } from "@/lib/api/gateway"
import type { Membership } from "@/lib/api/types"
import { sessionToken, useAuthStore } from "@/lib/auth/store"
import { qk } from "@/lib/query"

export function useMe() {
  const token = useAuthStore((s) => s.token)
  return useQuery({
    queryKey: qk.me,
    queryFn: () => gateway.auth.me(sessionToken()),
    enabled: Boolean(token),
    staleTime: 60_000,
  })
}

export interface Workspace {
  isLoading: boolean
  error: unknown
  memberships: Membership[]
  membership: Membership | null
  teamId: string | null
  isTeamAdmin: boolean
  isPlatformAdmin: boolean
}

/** The signed-in user's teams and the one selected in the team switcher. Roles come fresh
 *  from the gateway's /v1/me; the UI uses them only to hide what the backend would refuse. */
export function useWorkspace(): Workspace {
  const me = useMe()
  const activeTeamId = useAuthStore((s) => s.activeTeamId)
  const memberships = me.data?.teams ?? []
  const membership = memberships.find((m) => m.team_id === activeTeamId) ?? memberships[0] ?? null
  return {
    isLoading: me.isLoading,
    error: me.error,
    memberships,
    membership,
    teamId: membership?.team_id ?? null,
    isTeamAdmin: membership?.role === "admin",
    isPlatformAdmin: me.data?.user.is_platform_admin ?? false,
  }
}

/** For pages rendered inside the team guard, where a team is always selected. */
export function useTeamId(): string {
  const { teamId } = useWorkspace()
  if (!teamId) throw new Error("No team selected.")
  return teamId
}

export function useLogin() {
  const signIn = useAuthStore((s) => s.signIn)
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) =>
      gateway.auth.login(email, password),
    onSuccess: (session) => {
      queryClient.clear()
      signIn(session.access_token, session.expires_at, session.user)
    },
  })
}

export function useLogout() {
  const signOut = useAuthStore((s) => s.signOut)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  return useMutation({
    mutationFn: async () => {
      const token = useAuthStore.getState().token
      if (token) await gateway.auth.logout(token).catch(() => undefined)
    },
    onSettled: () => {
      signOut()
      queryClient.clear()
      navigate("/login", { replace: true })
    },
  })
}
