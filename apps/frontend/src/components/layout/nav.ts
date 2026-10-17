import {
  ActivityIcon,
  BarChart3Icon,
  KeyRoundIcon,
  LayoutDashboardIcon,
  MessageSquareIcon,
  ScrollTextIcon,
  ServerIcon,
  SettingsIcon,
  UsersIcon,
  WrenchIcon,
  type LucideIcon,
} from "lucide-react"

import type { Workspace } from "@/hooks/queries/use-session"

export type Access = "member" | "teamAdmin" | "platformAdmin"

export interface NavItem {
  title: string
  to: string
  icon: LucideIcon
  access: Access
}

export const NAV_GROUPS: Array<{ label: string; items: NavItem[] }> = [
  {
    label: "Platform",
    items: [
      { title: "Overview", to: "/overview", icon: LayoutDashboardIcon, access: "member" },
      { title: "Chat", to: "/chat", icon: MessageSquareIcon, access: "member" },
      { title: "MCP servers", to: "/mcp-servers", icon: ServerIcon, access: "member" },
      { title: "Tools", to: "/tools", icon: WrenchIcon, access: "member" },
    ],
  },
  {
    label: "Activity",
    items: [
      { title: "Usage", to: "/usage", icon: BarChart3Icon, access: "member" },
      { title: "Audit logs", to: "/audit-logs", icon: ScrollTextIcon, access: "member" },
      { title: "Agent metrics", to: "/agent-metrics", icon: ActivityIcon, access: "member" },
    ],
  },
  {
    label: "Manage",
    items: [
      { title: "Team", to: "/team", icon: UsersIcon, access: "teamAdmin" },
      { title: "API keys", to: "/api-keys", icon: KeyRoundIcon, access: "member" },
      { title: "Settings", to: "/settings", icon: SettingsIcon, access: "member" },
    ],
  },
]

/** UI-only check used to hide what the backend would refuse anyway. */
export function hasAccess(ws: Workspace, access: Access): boolean {
  if (access === "platformAdmin") return ws.isPlatformAdmin
  if (access === "teamAdmin") return ws.isTeamAdmin
  return ws.teamId !== null
}

export const PAGE_TITLES: Record<string, string> = Object.fromEntries(
  NAV_GROUPS.flatMap((g) => g.items.map((i) => [i.to, i.title]))
)
PAGE_TITLES["/profile"] = "Profile"
