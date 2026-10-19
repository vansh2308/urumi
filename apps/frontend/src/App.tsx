import { QueryClientProvider } from "@tanstack/react-query"
import { ThemeProvider } from "next-themes"
import { RouterProvider } from "react-router"

import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { setUnauthorizedHandler } from "@/lib/api/http"
import { useAuthStore } from "@/lib/auth/store"
import { queryClient } from "@/lib/query"
import { router } from "@/routes/router"

// A 401 on the session token means it expired or was revoked: drop it and go to sign-in.
// A 401 on a tool key (urk_) means it was revoked or expired: drop it, and the login's session
// key is issued again where it is needed.
setUnauthorizedHandler((token) => {
  const state = useAuthStore.getState()
  for (const [teamId, key] of Object.entries(state.toolKeys)) {
    if (key.key === token) state.setToolKey(teamId, null)
  }
  if (state.token === token) {
    state.signOut()
    queryClient.clear()
    void router.navigate(`/login?next=${encodeURIComponent(window.location.pathname)}`)
  }
})

export default function App() {
  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider delayDuration={300}>
          <RouterProvider router={router} />
          <Toaster richColors={false} position="bottom-right" />
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  )
}
