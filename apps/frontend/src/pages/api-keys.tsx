import type { ColumnDef } from "@tanstack/react-table"
import { InfoIcon, MoreHorizontalIcon, PlusIcon } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { ConfirmDialog } from "@/components/common/confirm-dialog"
import { CopyableId } from "@/components/common/copy-button"
import { PageHeader } from "@/components/common/page-header"
import { SecretRevealDialog } from "@/components/common/secret-reveal-dialog"
import { StatusBadge } from "@/components/common/status-badge"
import { DataTable } from "@/components/data-table/data-table"
import { PageBody } from "@/components/layout/app-shell"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { CreateKeyDialog } from "@/features/api-keys/create-key-dialog"
import { RotateKeyDialog } from "@/features/api-keys/rotate-key-dialog"
import { ConnectMcpCard } from "@/features/mcp-endpoint/connect-mcp-card"
import { useToolKey } from "@/features/tool-key/use-tool-key"
import { useApiKeys, useRevokeApiKey } from "@/hooks/queries/use-api-keys"
import { useMemberNames } from "@/hooks/queries/use-member-names"
import { useMe, useTeamId, useWorkspace } from "@/hooks/queries/use-session"
import { useTeam } from "@/hooks/queries/use-team"
import { useSearchState } from "@/hooks/use-search-state"
import { errorMessage } from "@/lib/api/http"
import type { ApiKey, IssuedApiKey } from "@/lib/api/types"
import { formatDateTime, formatRelative } from "@/lib/format"

export default function ApiKeysPage() {
  const teamId = useTeamId()
  const { isTeamAdmin } = useWorkspace()
  const me = useMe()
  const team = useTeam(teamId)
  const { get, set } = useSearchState()
  const includeInactive = get("inactive") === "1"
  const keys = useApiKeys(teamId, includeInactive)
  const revoke = useRevokeApiKey(teamId)
  const nameOf = useMemberNames(teamId)
  const { toolKey, clear: clearToolKey } = useToolKey(teamId)
  const [creating, setCreating] = useState(false)
  const [rotating, setRotating] = useState<ApiKey | null>(null)
  const [revoking, setRevoking] = useState<ApiKey | null>(null)
  const [issued, setIssued] = useState<IssuedApiKey | null>(null)

  const userId = me.data?.user.id
  const canCreate = isTeamAdmin || Boolean(team.data?.members_can_create_keys)

  const columns: ColumnDef<ApiKey, unknown>[] = [
    {
      accessorKey: "name",
      header: "Name",
      cell: ({ row }) => (
        <div>
          <div className="font-medium">{row.original.name}</div>
          <div className="font-mono text-xs text-muted-foreground">{row.original.prefix}…</div>
        </div>
      ),
    },
    {
      accessorKey: "status",
      header: "Status",
      cell: ({ row }) => <StatusBadge status={row.original.status} />,
    },
    {
      id: "scopes",
      accessorFn: (k) => k.scopes.join(" "),
      header: "Scopes",
      enableSorting: false,
      cell: ({ row }) => (
        <div className="flex flex-wrap gap-1">
          {row.original.scopes.map((s) => (
            <Badge key={s} variant="outline" className="font-mono font-normal">
              {s}
            </Badge>
          ))}
        </div>
      ),
    },
    {
      id: "restrictions",
      accessorFn: (k) => k.tool_restrictions?.join(" ") ?? "",
      header: "Tool restrictions",
      enableSorting: false,
      cell: ({ row }) =>
        row.original.tool_restrictions ? (
          <Tooltip>
            <TooltipTrigger className="text-left underline decoration-dotted underline-offset-4">
              {row.original.tool_restrictions.length} rule(s)
            </TooltipTrigger>
            <TooltipContent className="font-mono">
              {row.original.tool_restrictions.map((r) => (
                <div key={r}>{r}</div>
              ))}
            </TooltipContent>
          </Tooltip>
        ) : (
          <span className="text-muted-foreground">Owner's access</span>
        ),
    },
    {
      id: "owner",
      accessorFn: (k) => nameOf(k.owner_user_id),
      header: "Owner",
    },
    {
      accessorKey: "last_used_at",
      header: "Last used",
      cell: ({ row }) => (
        <span title={formatDateTime(row.original.last_used_at)}>{formatRelative(row.original.last_used_at)}</span>
      ),
    },
    {
      accessorKey: "expires_at",
      header: "Expires",
      cell: ({ row }) => <span className="whitespace-nowrap">{formatDateTime(row.original.expires_at)}</span>,
    },
    {
      accessorKey: "created_at",
      header: "Created",
      cell: ({ row }) => <span className="whitespace-nowrap">{formatDateTime(row.original.created_at)}</span>,
    },
    {
      accessorKey: "id",
      header: "Key ID",
      enableSorting: false,
      cell: ({ row }) => <CopyableId value={row.original.id} />,
    },
    {
      id: "actions",
      enableHiding: false,
      enableSorting: false,
      cell: ({ row }) => {
        const k = row.original
        if (k.status !== "active") return null
        const isOwner = k.owner_user_id === userId
        return (
          <div className="flex justify-end">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${k.name}`}>
                  <MoreHorizontalIcon />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {isOwner && canCreate && (
                  <DropdownMenuItem onClick={() => setRotating(k)}>Rotate</DropdownMenuItem>
                )}
                <DropdownMenuItem variant="destructive" onClick={() => setRevoking(k)}>
                  Revoke
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        )
      },
    },
  ]

  return (
    <PageBody>
      <PageHeader
        title="API keys"
        description={
          isTeamAdmin
            ? "Platform API keys (urk_) for this team. You see every member's keys."
            : "Your platform API keys (urk_) for this team."
        }
        actions={
          <Button size="sm" onClick={() => setCreating(true)} disabled={!canCreate}>
            <PlusIcon /> Create key
          </Button>
        }
      />
      {!canCreate && team.data && (
        <Alert>
          <InfoIcon />
          <AlertDescription>
            This team does not let members create API keys. Ask a team admin to change the team
            settings or issue one.
          </AlertDescription>
        </Alert>
      )}
      <DataTable
        columns={columns}
        data={keys.data}
        isLoading={keys.isLoading}
        error={keys.error}
        onRetry={() => void keys.refetch()}
        getRowId={(k) => k.id}
        searchPlaceholder="Search keys…"
        initialHidden={{ created_at: false, id: false, ...(isTeamAdmin ? {} : { owner: false }) }}
        emptyTitle="No API keys"
        emptyDescription="Keys let scripts, MCP clients and the agent call tools as you."
        toolbar={
          <div className="flex items-center gap-2">
            <Switch
              id="inactive"
              checked={includeInactive}
              onCheckedChange={(c) => set({ inactive: c ? "1" : null })}
            />
            <Label htmlFor="inactive" className="font-normal">
              Show revoked and expired
            </Label>
          </div>
        }
      />

      <ConnectMcpCard />

      <CreateKeyDialog
        open={creating}
        onOpenChange={setCreating}
        teamId={teamId}
        maxTtlDays={team.data?.max_key_ttl_days}
        onIssued={setIssued}
      />
      <RotateKeyDialog
        apiKey={rotating}
        teamId={teamId}
        onOpenChange={(o) => !o && setRotating(null)}
        onIssued={(k) => {
          if (toolKey?.keyId === rotating?.id) clearToolKey()
          setIssued(k)
        }}
      />
      <ConfirmDialog
        open={revoking !== null}
        onOpenChange={(o) => !o && setRevoking(null)}
        title={`Revoke “${revoking?.name}”?`}
        description="Anything using this key (scripts, MCP clients, agent sessions) is refused immediately. This cannot be undone."
        confirmLabel="Revoke key"
        destructive
        pending={revoke.isPending}
        onConfirm={() =>
          revoking &&
          revoke.mutate(revoking.id, {
            onSuccess: () => {
              if (toolKey?.keyId === revoking.id) clearToolKey()
              toast.success(`Revoked ${revoking.name}.`)
              setRevoking(null)
            },
            onError: (e) => toast.error(errorMessage(e)),
          })
        }
      />
      <SecretRevealDialog
        secret={issued?.key ?? null}
        title={`API key “${issued?.name}”`}
        description={`Expires ${formatDateTime(issued?.expires_at)}. Use it as a Bearer token with the gateway or agent.`}
        onClose={() => setIssued(null)}
      />
    </PageBody>
  )
}
