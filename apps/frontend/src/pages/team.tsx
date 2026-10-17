import type { ColumnDef } from "@tanstack/react-table"
import { MoreHorizontalIcon, UserPlusIcon } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { ConfirmDialog } from "@/components/common/confirm-dialog"
import { CopyButton } from "@/components/common/copy-button"
import { PageHeader } from "@/components/common/page-header"
import { SecretRevealDialog } from "@/components/common/secret-reveal-dialog"
import { StatusBadge } from "@/components/common/status-badge"
import { DataTable } from "@/components/data-table/data-table"
import { PageBody } from "@/components/layout/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { InviteDialog } from "@/features/team/invite-dialog"
import { MemberAccessSheet } from "@/features/team/member-access-sheet"
import { useMe, useTeamId, useWorkspace } from "@/hooks/queries/use-session"
import {
  useChangeRole,
  useInvitations,
  useMembers,
  useRemoveMember,
  useRevokeInvitation,
} from "@/hooks/queries/use-team"
import { useSearchState } from "@/hooks/use-search-state"
import { errorMessage } from "@/lib/api/http"
import type { Invitation, IssuedInvitation, Member, TeamRole } from "@/lib/api/types"
import { formatDateTime } from "@/lib/format"

export default function TeamPage() {
  const teamId = useTeamId()
  const { membership } = useWorkspace()
  const me = useMe()
  const { get, set } = useSearchState()
  const tab = get("tab") === "invitations" ? "invitations" : "members"
  const includeClosed = get("closed") === "1"
  const members = useMembers(teamId)
  const invitations = useInvitations(teamId, includeClosed)
  const changeRole = useChangeRole(teamId)
  const removeMember = useRemoveMember(teamId)
  const revokeInvitation = useRevokeInvitation(teamId)
  const [inviting, setInviting] = useState(false)
  const [issued, setIssued] = useState<IssuedInvitation | null>(null)
  const [roleChange, setRoleChange] = useState<{ member: Member; role: TeamRole } | null>(null)
  const [removing, setRemoving] = useState<Member | null>(null)
  const [revoking, setRevoking] = useState<Invitation | null>(null)
  const [accessFor, setAccessFor] = useState<Member | null>(null)

  const memberColumns: ColumnDef<Member, unknown>[] = [
    {
      accessorKey: "display_name",
      header: "Name",
      cell: ({ row }) => (
        <div>
          <div className="font-medium">
            {row.original.display_name}
            {row.original.user_id === me.data?.user.id && (
              <span className="ml-1 text-muted-foreground">(you)</span>
            )}
          </div>
          <div className="text-xs text-muted-foreground">{row.original.email}</div>
        </div>
      ),
    },
    {
      accessorKey: "role",
      header: "Role",
      cell: ({ row }) => (
        <Badge variant={row.original.role === "admin" ? "secondary" : "outline"} className="capitalize">
          {row.original.role}
        </Badge>
      ),
    },
    {
      accessorKey: "joined_at",
      header: "Joined",
      cell: ({ row }) => formatDateTime(row.original.joined_at),
    },
    {
      id: "actions",
      enableHiding: false,
      enableSorting: false,
      cell: ({ row }) => {
        const m = row.original
        const other: TeamRole = m.role === "admin" ? "member" : "admin"
        return (
          <div className="flex justify-end">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${m.display_name}`}>
                  <MoreHorizontalIcon />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => setAccessFor(m)}>Manage tool access</DropdownMenuItem>
                <DropdownMenuItem onClick={() => setRoleChange({ member: m, role: other })}>
                  Make {other}
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem variant="destructive" onClick={() => setRemoving(m)}>
                  Remove from team
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        )
      },
    },
  ]

  const invitationColumns: ColumnDef<Invitation, unknown>[] = [
    { accessorKey: "email", header: "Email" },
    {
      accessorKey: "role",
      header: "Role",
      cell: ({ row }) => <span className="capitalize">{row.original.role}</span>,
    },
    {
      accessorKey: "status",
      header: "Status",
      cell: ({ row }) => <StatusBadge status={row.original.status} />,
    },
    { accessorKey: "created_at", header: "Sent", cell: ({ row }) => formatDateTime(row.original.created_at) },
    { accessorKey: "expires_at", header: "Expires", cell: ({ row }) => formatDateTime(row.original.expires_at) },
    {
      id: "actions",
      enableHiding: false,
      enableSorting: false,
      cell: ({ row }) =>
        row.original.status === "pending" ? (
          <div className="flex justify-end">
            <Button variant="ghost" size="sm" onClick={() => setRevoking(row.original)}>
              Revoke
            </Button>
          </div>
        ) : null,
    },
  ]

  const inviteLink = issued
    ? `${window.location.origin}/login?tab=register&invitation=${encodeURIComponent(issued.token)}`
    : ""

  return (
    <PageBody>
      <PageHeader
        title="Team"
        description={`Members, roles and invitations for ${membership?.team_name}.`}
        actions={
          <Button size="sm" onClick={() => setInviting(true)}>
            <UserPlusIcon /> Invite member
          </Button>
        }
      />
      <Tabs value={tab} onValueChange={(v) => set({ tab: v === "members" ? null : v, q: null, page: null })}>
        <TabsList>
          <TabsTrigger value="members">Members</TabsTrigger>
          <TabsTrigger value="invitations">Invitations</TabsTrigger>
        </TabsList>
        <TabsContent value="members" className="pt-3">
          <DataTable
            columns={memberColumns}
            data={members.data}
            isLoading={members.isLoading}
            error={members.error}
            onRetry={() => void members.refetch()}
            getRowId={(m) => m.user_id}
            searchPlaceholder="Search members…"
            emptyTitle="No members"
          />
        </TabsContent>
        <TabsContent value="invitations" className="pt-3">
          <DataTable
            columns={invitationColumns}
            data={invitations.data}
            isLoading={invitations.isLoading}
            error={invitations.error}
            onRetry={() => void invitations.refetch()}
            getRowId={(i) => i.id}
            searchPlaceholder="Search invitations…"
            emptyTitle="No open invitations"
            emptyDescription="Invite teammates by email; they join with the token you share."
            toolbar={
              <div className="flex items-center gap-2">
                <Switch
                  id="closed"
                  checked={includeClosed}
                  onCheckedChange={(c) => set({ closed: c ? "1" : null })}
                />
                <Label htmlFor="closed" className="font-normal">
                  Include accepted, revoked and expired
                </Label>
              </div>
            }
          />
        </TabsContent>
      </Tabs>

      <InviteDialog open={inviting} onOpenChange={setInviting} teamId={teamId} onIssued={setIssued} />
      <SecretRevealDialog
        secret={issued?.token ?? null}
        title={`Invitation for ${issued?.email}`}
        description={`Joins as ${issued?.role}. Expires ${formatDateTime(issued?.expires_at)}. Share the token or the sign-up link below.`}
        onClose={() => setIssued(null)}
      >
        <div className="flex items-center gap-2 rounded-md border p-2 text-xs">
          <span className="min-w-0 flex-1 truncate font-mono" title={inviteLink}>
            {inviteLink}
          </span>
          <CopyButton value={inviteLink} label="Copy sign-up link" />
        </div>
        <p className="text-xs text-muted-foreground">
          Existing users can paste the token on their Profile page instead.
        </p>
      </SecretRevealDialog>
      <ConfirmDialog
        open={roleChange !== null}
        onOpenChange={(o) => !o && setRoleChange(null)}
        title={`Make ${roleChange?.member.display_name} ${roleChange?.role === "admin" ? "an admin" : "a member"}?`}
        description={
          roleChange?.role === "admin"
            ? "Admins manage members, invitations, API keys, MCP server access and team settings."
            : "They lose admin rights and keep only the tools granted to them. A team must keep at least one admin."
        }
        confirmLabel="Change role"
        pending={changeRole.isPending}
        onConfirm={() =>
          roleChange &&
          changeRole.mutate(
            { userId: roleChange.member.user_id, role: roleChange.role },
            {
              onSuccess: () => {
                toast.success("Role updated.")
                setRoleChange(null)
              },
              onError: (e) => toast.error(errorMessage(e)),
            }
          )
        }
      />
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(o) => !o && setRemoving(null)}
        title={`Remove ${removing?.display_name} from the team?`}
        description="Their tool grants and team API keys stop working immediately."
        confirmLabel="Remove member"
        destructive
        pending={removeMember.isPending}
        onConfirm={() =>
          removing &&
          removeMember.mutate(removing.user_id, {
            onSuccess: () => {
              toast.success(`${removing.display_name} removed.`)
              setRemoving(null)
            },
            onError: (e) => toast.error(errorMessage(e)),
          })
        }
      />
      <ConfirmDialog
        open={revoking !== null}
        onOpenChange={(o) => !o && setRevoking(null)}
        title={`Revoke the invitation for ${revoking?.email}?`}
        description="The token stops working. You can invite them again later."
        confirmLabel="Revoke invitation"
        destructive
        pending={revokeInvitation.isPending}
        onConfirm={() =>
          revoking &&
          revokeInvitation.mutate(revoking.id, {
            onSuccess: () => {
              toast.success("Invitation revoked.")
              setRevoking(null)
            },
            onError: (e) => toast.error(errorMessage(e)),
          })
        }
      />
      <MemberAccessSheet teamId={teamId} member={accessFor} onOpenChange={(o) => !o && setAccessFor(null)} />
    </PageBody>
  )
}
