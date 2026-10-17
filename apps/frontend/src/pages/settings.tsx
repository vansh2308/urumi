import { MonitorIcon, MoonIcon, PlusIcon, SunIcon } from "lucide-react"
import { useTheme } from "next-themes"
import { useState } from "react"

import { PageHeader } from "@/components/common/page-header"
import { ErrorAlert } from "@/components/common/query-state"
import { PageBody } from "@/components/layout/app-shell"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { CreateTeamDialog } from "@/features/team/create-team-dialog"
import { TeamSettingsForm } from "@/features/team/team-settings-form"
import { ToolKeyStatus } from "@/features/tool-key/tool-key-status"
import { useTeamId, useWorkspace } from "@/hooks/queries/use-session"
import { useTeam } from "@/hooks/queries/use-team"
import { formatDateTime } from "@/lib/format"

export default function SettingsPage() {
  const teamId = useTeamId()
  const { isTeamAdmin } = useWorkspace()
  const team = useTeam(teamId)
  const { theme, setTheme } = useTheme()
  const [creating, setCreating] = useState(false)

  return (
    <PageBody>
      <PageHeader title="Settings" description="Team settings and your preferences." />

      <Card>
        <CardHeader>
          <CardTitle>Team settings</CardTitle>
          <CardDescription>
            {isTeamAdmin
              ? "Applies to everyone in the team."
              : "Only team admins can change these."}
            {team.data && ` Created ${formatDateTime(team.data.created_at)} · slug ${team.data.slug}`}
          </CardDescription>
          <CardAction>
            <Button variant="outline" size="sm" onClick={() => setCreating(true)}>
              <PlusIcon /> New team
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent>
          {team.error ? (
            <ErrorAlert error={team.error} />
          ) : team.data ? (
            <TeamSettingsForm team={team.data} editable={isTeamAdmin} />
          ) : (
            <Skeleton className="h-48 w-full" />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Appearance</CardTitle>
          <CardDescription>Stored in this browser.</CardDescription>
        </CardHeader>
        <CardContent>
          <ToggleGroup
            type="single"
            variant="outline"
            value={theme ?? "system"}
            onValueChange={(v) => v && setTheme(v)}
            aria-label="Theme"
          >
            <ToggleGroupItem value="light">
              <SunIcon /> Light
            </ToggleGroupItem>
            <ToggleGroupItem value="dark">
              <MoonIcon /> Dark
            </ToggleGroupItem>
            <ToggleGroupItem value="system">
              <MonitorIcon /> System
            </ToggleGroupItem>
          </ToggleGroup>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>API key for chat and tool runs</CardTitle>
          <CardDescription>
            The agent and the gateway's tool endpoint only accept platform API keys. The key you
            connect is kept in this browser tab and forgotten when it closes or you sign out.
          </CardDescription>
          <CardAction>
            <ToolKeyStatus teamId={teamId} />
          </CardAction>
        </CardHeader>
      </Card>

      <CreateTeamDialog open={creating} onOpenChange={setCreating} />
    </PageBody>
  )
}
