// Client-side session state. The gateway session token (ugs_) is the only credential for
// management APIs; the backend re-checks it and the caller's role on every request, so nothing
// stored here grants access by itself.

import { create } from "zustand"
import { createJSONStorage, persist } from "zustand/middleware"

import type { User } from "@/lib/api/types"

/** A platform API key (urk_) for the agent chat and tool runs: the login session's own key
 *  (issued automatically) or one the user pasted. */
export interface ToolKey {
  key: string
  keyId: string | null
  prefix: string
  name: string
  expiresAt: string | null
  /** Session keys die with the login (logout revokes them), so they may be kept across tabs. */
  sessionBound?: boolean
}

interface AuthState {
  token: string | null
  expiresAt: string | null
  user: User | null
  activeTeamId: string | null
  /** Per team. Session keys persist with the login (localStorage); pasted keys stay in this
   *  tab only (sessionStorage). */
  toolKeys: Record<string, ToolKey>
  signIn: (token: string, expiresAt: string, user: User) => void
  signOut: () => void
  setUser: (user: User) => void
  setActiveTeam: (teamId: string | null) => void
  setToolKey: (teamId: string, key: ToolKey | null) => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      expiresAt: null,
      user: null,
      activeTeamId: null,
      toolKeys: {},
      signIn: (token, expiresAt, user) => set({ token, expiresAt, user }),
      signOut: () => {
        set({ token: null, expiresAt: null, user: null, toolKeys: {} })
        sessionStorage.removeItem(TOOL_KEY_STORAGE)
      },
      setUser: (user) => set({ user }),
      setActiveTeam: (activeTeamId) => set({ activeTeamId }),
      setToolKey: (teamId, key) =>
        set((s) => {
          const toolKeys = { ...s.toolKeys }
          if (key) toolKeys[teamId] = key
          else delete toolKeys[teamId]
          return { toolKeys }
        }),
    }),
    {
      name: "urumi.auth",
      storage: createJSONStorage(() => localStorage),
      partialize: (s) => ({
        token: s.token,
        expiresAt: s.expiresAt,
        user: s.user,
        activeTeamId: s.activeTeamId,
        toolKeys: Object.fromEntries(
          Object.entries(s.toolKeys).filter(([, k]) => k.sessionBound)
        ),
      }),
    }
  )
)

const TOOL_KEY_STORAGE = "urumi.toolKeys"

// A tab's own choice (e.g. a pasted key) lives in sessionStorage and wins over the session key.
try {
  const raw = sessionStorage.getItem(TOOL_KEY_STORAGE)
  if (raw) useAuthStore.setState((s) => ({ toolKeys: { ...s.toolKeys, ...JSON.parse(raw) } }))
} catch {
  sessionStorage.removeItem(TOOL_KEY_STORAGE)
}
useAuthStore.subscribe((state, prev) => {
  if (state.toolKeys !== prev.toolKeys) {
    sessionStorage.setItem(TOOL_KEY_STORAGE, JSON.stringify(state.toolKeys))
  }
})

export function isSessionValid(state: Pick<AuthState, "token" | "expiresAt">): boolean {
  if (!state.token) return false
  if (!state.expiresAt) return true
  return new Date(state.expiresAt).getTime() > Date.now()
}

/** The session token, for query functions. Routes under the auth guard always have one. */
export function sessionToken(): string {
  const token = useAuthStore.getState().token
  if (!token) throw new Error("Not signed in.")
  return token
}
