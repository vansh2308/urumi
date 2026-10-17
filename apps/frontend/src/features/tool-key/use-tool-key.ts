import { useAuthStore, type ToolKey } from "@/lib/auth/store"

/** The API key this browser tab uses for agent chat and tool runs in the given team. */
export function useToolKey(teamId: string) {
  const key = useAuthStore((s) => s.toolKeys[teamId] ?? null)
  const setToolKey = useAuthStore((s) => s.setToolKey)
  const expired = key?.expiresAt ? new Date(key.expiresAt).getTime() <= Date.now() : false
  return {
    toolKey: expired ? null : key,
    setToolKey: (k: ToolKey | null) => setToolKey(teamId, k),
    clear: () => setToolKey(teamId, null),
  }
}
