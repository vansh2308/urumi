import { lazy, Suspense, type ReactNode } from "react"
import { createBrowserRouter, Navigate } from "react-router"

import { AppShell } from "@/components/layout/app-shell"
import { Spinner } from "@/components/ui/spinner"
import { ForbiddenPage, NotFoundPage } from "@/pages/errors"
import { LoginPage } from "@/pages/login"

import { RedirectIfSignedIn, RequireAccess, RequireAuth, RequireTeam } from "./guards"

const OverviewPage = lazy(() => import("@/pages/overview"))
const ChatPage = lazy(() => import("@/pages/chat"))
const McpServersPage = lazy(() => import("@/pages/mcp-servers"))
const McpServerDetailPage = lazy(() => import("@/pages/mcp-server-detail"))
const ToolsPage = lazy(() => import("@/pages/tools"))
const UsagePage = lazy(() => import("@/pages/usage"))
const AuditLogsPage = lazy(() => import("@/pages/audit-logs"))
const AgentMetricsPage = lazy(() => import("@/pages/agent-metrics"))
const TeamPage = lazy(() => import("@/pages/team"))
const ApiKeysPage = lazy(() => import("@/pages/api-keys"))
const SettingsPage = lazy(() => import("@/pages/settings"))
const ProfilePage = lazy(() => import("@/pages/profile"))
const OAuthConsentPage = lazy(() => import("@/pages/oauth-consent"))

function page(node: ReactNode) {
  return (
    <Suspense
      fallback={
        <div className="flex flex-1 items-center justify-center p-10">
          <Spinner className="size-5" />
        </div>
      }
    >
      {node}
    </Suspense>
  )
}

export const router = createBrowserRouter([
  {
    path: "/login",
    element: (
      <RedirectIfSignedIn>
        <LoginPage />
      </RedirectIfSignedIn>
    ),
  },
  {
    element: <RequireAuth />,
    children: [
      {
        element: <RequireTeam />,
        children: [
          // OAuth consent is a full page (no sidebar): the user arrives from an MCP client.
          { path: "oauth/consent", element: page(<OAuthConsentPage />) },
          {
            element: <AppShell />,
            children: [
              { index: true, element: <Navigate to="/overview" replace /> },
              { path: "overview", element: page(<OverviewPage />) },
              { path: "chat/:conversationId?", element: page(<ChatPage />) },
              { path: "mcp-servers", element: page(<McpServersPage />) },
              { path: "mcp-servers/:serverId", element: page(<McpServerDetailPage />) },
              { path: "tools", element: page(<ToolsPage />) },
              { path: "usage", element: page(<UsagePage />) },
              { path: "audit-logs", element: page(<AuditLogsPage />) },
              { path: "agent-metrics", element: page(<AgentMetricsPage />) },
              {
                element: <RequireAccess access="teamAdmin" />,
                children: [{ path: "team", element: page(<TeamPage />) }],
              },
              { path: "api-keys", element: page(<ApiKeysPage />) },
              { path: "settings", element: page(<SettingsPage />) },
              { path: "profile", element: page(<ProfilePage />) },
              { path: "403", element: <ForbiddenPage /> },
              { path: "*", element: <NotFoundPage /> },
            ],
          },
        ],
      },
    ],
  },
])
