import { useEffect, useState } from "react"

import { gateway } from "@/lib/api/gateway"
import { useAuthStore, type ToolKey } from "@/lib/auth/store"

const inFlight = new Map<string, Promise<void>>()

function isUsable(key: ToolKey | null | undefined): key is ToolKey {
  return Boolean(key && (!key.expiresAt || new Date(key.expiresAt).getTime() > Date.now()))
}

/** Ask the gateway for this login session's key in a team (replacing any previous one). Shared
 *  by every component and tab state, so concurrent callers wait for one request. */
export function issueSessionKey(teamId: string): Promise<void> {
  const running = inFlight.get(teamId)
  if (running) return running
  const work = (async () => {
    const { token, setToolKey } = useAuthStore.getState()
    if (!token) return
    const issued = await gateway.apiKeys.session(token, teamId)
    setToolKey(teamId, {
      key: issued.key,
      keyId: issued.id,
      prefix: issued.prefix,
      name: issued.name,
      expiresAt: issued.expires_at,
      sessionBound: true,
    })
  })().finally(() => inFlight.delete(teamId))
  inFlight.set(teamId, work)
  return work
}

/**
 * The API key the agent chat and tool runs use in a team. Without a usable one, the login
 * session's key is issued automatically, so nobody has to paste a key; conversations belong to
 * the user, not the key, so history survives a new key. `clear()` (e.g. after a 401) drops the
 * key and a fresh one is issued.
 */
export function useToolKey(teamId: string) {
  const key = useAuthStore((s) => s.toolKeys[teamId] ?? null)
  const token = useAuthStore((s) => s.token)
  const setToolKey = useAuthStore((s) => s.setToolKey)
  const usable = isUsable(key)
  // The team whose automatic issue failed, so the UI can offer the manual dialog instead.
  const [failedFor, setFailedFor] = useState<string | null>(null)

  useEffect(() => {
    if (usable || !token || !teamId) return
    issueSessionKey(teamId).then(
      () => setFailedFor(null),
      () => setFailedFor(teamId)
    )
  }, [usable, token, teamId])

  return {
    toolKey: usable ? key : null,
    /** A session key is being issued. False once it failed: offer the manual dialog then. */
    connecting: !usable && Boolean(token) && failedFor !== teamId,
    setToolKey: (k: ToolKey | null) => setToolKey(teamId, k),
    clear: () => setToolKey(teamId, null),
    renew: () => issueSessionKey(teamId),
  }
}
