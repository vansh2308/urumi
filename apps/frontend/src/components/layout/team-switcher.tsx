import { useQueryClient } from "@tanstack/react-query"
import { CheckIcon, ChevronsUpDownIcon, PlusIcon, UsersIcon } from "lucide-react"
import { useState } from "react"

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { SidebarMenu, SidebarMenuButton, SidebarMenuItem } from "@/components/ui/sidebar"
import { CreateTeamDialog } from "@/features/team/create-team-dialog"
import { useWorkspace } from "@/hooks/queries/use-session"
import { useAuthStore } from "@/lib/auth/store"

export function TeamSwitcher() {
  const { memberships, membership } = useWorkspace()
  const setActiveTeam = useAuthStore((s) => s.setActiveTeam)
  const queryClient = useQueryClient()
  const [creating, setCreating] = useState(false)

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton size="lg">
              <div className="flex aspect-square size-8 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
                <UsersIcon className="size-4" />
              </div>
              <div className="grid flex-1 text-left text-sm leading-tight">
                <span className="truncate font-medium">{membership?.team_name ?? "No team"}</span>
                <span className="truncate text-xs text-muted-foreground capitalize">
                  {membership?.role ?? "—"}
                </span>
              </div>
              <ChevronsUpDownIcon className="ml-auto" />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent className="min-w-56" align="start">
            <DropdownMenuLabel className="text-xs text-muted-foreground">Teams</DropdownMenuLabel>
            {memberships.map((m) => (
              <DropdownMenuItem
                key={m.team_id}
                onClick={() => {
                  setActiveTeam(m.team_id)
                  void queryClient.invalidateQueries({ queryKey: ["teams"] })
                }}
              >
                <span className="flex-1 truncate">{m.team_name}</span>
                <span className="text-xs text-muted-foreground capitalize">{m.role}</span>
                {m.team_id === membership?.team_id && <CheckIcon />}
              </DropdownMenuItem>
            ))}
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => setCreating(true)}>
              <PlusIcon /> Create team
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <CreateTeamDialog open={creating} onOpenChange={setCreating} />
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
