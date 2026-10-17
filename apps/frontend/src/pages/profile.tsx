import { CheckIcon, LogOutIcon, ShieldCheckIcon } from "lucide-react"
import { useQueryClient } from "@tanstack/react-query"

import { CopyableId } from "@/components/common/copy-button"
import { PageHeader } from "@/components/common/page-header"
import { PageBody } from "@/components/layout/app-shell"
import { initials } from "@/components/layout/user-menu"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { AcceptInvitationForm } from "@/features/team/accept-invitation-form"
import { useLogout, useMe, useWorkspace } from "@/hooks/queries/use-session"
import { useAuthStore } from "@/lib/auth/store"
import { formatDateTime, formatRelative } from "@/lib/format"

export default function ProfilePage() {
  const { data } = useMe()
  const { membership } = useWorkspace()
  const expiresAt = useAuthStore((s) => s.expiresAt)
  const setActiveTeam = useAuthStore((s) => s.setActiveTeam)
  const queryClient = useQueryClient()
  const logout = useLogout()
  const user = data?.user

  return (
    <PageBody>
      <PageHeader title="Profile" description="Your account, session and team memberships." />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex flex-row items-center gap-4">
            <Avatar className="size-12">
              <AvatarFallback>{initials(user?.display_name ?? "?")}</AvatarFallback>
            </Avatar>
            <div className="min-w-0">
              <CardTitle className="truncate">{user?.display_name}</CardTitle>
              <CardDescription className="truncate">{user?.email}</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div className="flex justify-between gap-2">
              <span className="text-muted-foreground">User ID</span>
              {user && <CopyableId value={user.id} />}
            </div>
            <div className="flex justify-between gap-2">
              <span className="text-muted-foreground">Member since</span>
              <span>{formatDateTime(user?.created_at)}</span>
            </div>
            <div className="flex justify-between gap-2">
              <span className="text-muted-foreground">Platform role</span>
              {user?.is_platform_admin ? (
                <Badge variant="secondary">
                  <ShieldCheckIcon /> Platform admin
                </Badge>
              ) : (
                <span>User</span>
              )}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Session</CardTitle>
            <CardDescription>Signed in with a gateway session token.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 text-sm">
            <div className="flex justify-between gap-2">
              <span className="text-muted-foreground">Expires</span>
              <span title={formatDateTime(expiresAt)}>{formatRelative(expiresAt)}</span>
            </div>
            <Button variant="outline" onClick={() => logout.mutate()} disabled={logout.isPending}>
              <LogOutIcon /> Sign out (revokes this session)
            </Button>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Teams</CardTitle>
          <CardDescription>Your role in each team. Switch the active team here or from the sidebar.</CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Team</TableHead>
                <TableHead>Slug</TableHead>
                <TableHead>Role</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {data?.teams.map((t) => (
                <TableRow key={t.team_id}>
                  <TableCell className="font-medium">{t.team_name}</TableCell>
                  <TableCell className="font-mono text-xs">{t.team_slug}</TableCell>
                  <TableCell className="capitalize">{t.role}</TableCell>
                  <TableCell className="text-right">
                    {t.team_id === membership?.team_id ? (
                      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                        <CheckIcon className="size-3" /> Active
                      </span>
                    ) : (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setActiveTeam(t.team_id)
                          void queryClient.invalidateQueries({ queryKey: ["teams"] })
                        }}
                      >
                        Switch
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Join another team</CardTitle>
          <CardDescription>Paste an invitation token you received from a team admin.</CardDescription>
        </CardHeader>
        <CardContent>
          <AcceptInvitationForm />
        </CardContent>
      </Card>
    </PageBody>
  )
}
