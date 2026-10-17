import { useMemo } from "react"

import { useMe, useWorkspace } from "./use-session"
import { useMembers } from "./use-team"

/** user_id -> display name. Only admins can list members; others see their own name. */
export function useMemberNames(teamId: string): (userId: string | null) => string {
  const { isTeamAdmin } = useWorkspace()
  const me = useMe()
  const members = useMembers(teamId, isTeamAdmin)
  const names = useMemo(() => {
    const map = new Map<string, string>()
    for (const m of members.data ?? []) map.set(m.user_id, m.display_name)
    if (me.data) map.set(me.data.user.id, `${me.data.user.display_name} (you)`)
    return map
  }, [members.data, me.data])
  return (userId) => (userId ? (names.get(userId) ?? userId.slice(0, 8)) : "System")
}
