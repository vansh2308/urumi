import { Navigate, Outlet, useLocation } from "react-router"

import { hasAccess, type Access } from "@/components/layout/nav"
import { ErrorAlert } from "@/components/common/query-state"
import { Spinner } from "@/components/ui/spinner"
import { useWorkspace } from "@/hooks/queries/use-session"
import { isSessionValid, useAuthStore } from "@/lib/auth/store"
import { NoTeamPage } from "@/pages/no-team"

function FullPageSpinner() {
  return (
    <div className="flex min-h-svh items-center justify-center">
      <Spinner className="size-6" />
    </div>
  )
}

/** Signed-in users only. The gateway still verifies the token on every request. */
export function RequireAuth() {
  const token = useAuthStore((s) => s.token)
  const expiresAt = useAuthStore((s) => s.expiresAt)
  const location = useLocation()
  if (!isSessionValid({ token, expiresAt })) {
    const next = `${location.pathname}${location.search}`
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />
  }
  return <Outlet />
}

/** Loads /v1/me and requires membership of at least one team. */
export function RequireTeam() {
  const ws = useWorkspace()
  if (ws.isLoading) return <FullPageSpinner />
  if (ws.error) {
    return (
      <div className="mx-auto max-w-lg p-6">
        <ErrorAlert error={ws.error} title="Could not load your account" />
      </div>
    )
  }
  if (!ws.teamId) return <NoTeamPage />
  return <Outlet />
}

/** Hides pages the role cannot use. Not a security boundary: the API enforces roles. */
export function RequireAccess({ access }: { access: Access }) {
  const ws = useWorkspace()
  if (!hasAccess(ws, access)) return <Navigate to="/403" replace />
  return <Outlet />
}

export function RedirectIfSignedIn({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token)
  const expiresAt = useAuthStore((s) => s.expiresAt)
  const location = useLocation()
  if (isSessionValid({ token, expiresAt })) {
    const next = new URLSearchParams(location.search).get("next")
    return <Navigate to={next && next.startsWith("/") && !next.startsWith("//") ? next : "/overview"} replace />
  }
  return children
}
