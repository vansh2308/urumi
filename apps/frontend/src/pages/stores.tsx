import type { ColumnDef } from "@tanstack/react-table"
import { ExternalLinkIcon, MoreHorizontalIcon, PlusIcon, RefreshCwIcon, StoreIcon } from "lucide-react"
import { useState } from "react"
import { Link, useNavigate } from "react-router"
import { toast } from "sonner"

import { PageHeader } from "@/components/common/page-header"
import { EmptyState } from "@/components/common/query-state"
import { DataTable } from "@/components/data-table/data-table"
import { PageBody } from "@/components/layout/app-shell"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { AdminPasswordDialog } from "@/features/stores/admin-password-dialog"
import { CreateStoreDialog } from "@/features/stores/create-store-dialog"
import { DeleteStoreDialog } from "@/features/stores/delete-store-dialog"
import { StorePhaseBadge } from "@/features/stores/store-status"
import { useMe, useTeamId, useWorkspace } from "@/hooks/queries/use-session"
import { useRetryStore, useStoreSettings, useStores } from "@/hooks/queries/use-stores"
import { errorMessage } from "@/lib/api/http"
import type { CreatedStore, Store } from "@/lib/api/types"
import { formatDateTime, formatRelative } from "@/lib/format"

export default function StoresPage() {
  const teamId = useTeamId()
  const { isTeamAdmin } = useWorkspace()
  const me = useMe()
  const userId = me.data?.user.id
  const navigate = useNavigate()
  const settings = useStoreSettings()
  const enabled = settings.data?.enabled ?? false
  const stores = useStores(teamId, enabled)
  const retry = useRetryStore(teamId)
  const [creating, setCreating] = useState(false)
  const [created, setCreated] = useState<CreatedStore | null>(null)
  const [deleting, setDeleting] = useState<Store | null>(null)

  const mine = (stores.data ?? []).filter((s) => s.created_by_user_id === userId).length
  const atCap = settings.data !== undefined && mine >= settings.data.max_per_user
  const canManage = (s: Store) => isTeamAdmin || s.created_by_user_id === userId

  const runRetry = (s: Store) =>
    retry.mutate(s.id, {
      onSuccess: () => toast.success(`Provisioning ${s.name} again.`),
      onError: (e) => toast.error(errorMessage(e)),
    })

  const columns: ColumnDef<Store, unknown>[] = [
    {
      id: "name",
      accessorFn: (s) => `${s.name} ${s.slug}`,
      header: "Store",
      cell: ({ row }) => (
        <Link to={`/stores/${row.original.id}`} className="block min-w-0 hover:underline">
          <div className="font-medium">{row.original.name}</div>
          <div className="font-mono text-xs text-muted-foreground">{row.original.slug}</div>
        </Link>
      ),
    },
    {
      id: "status",
      accessorFn: (s) => s.phase,
      header: "Status",
      cell: ({ row }) => {
        const s = row.original
        const badge = <StorePhaseBadge store={s} />
        if (s.phase !== "Failed" || !s.status_reason) return badge
        return (
          <Tooltip>
            <TooltipTrigger asChild>
              <div className="space-y-1">
                {badge}
                <p className="line-clamp-1 max-w-72 text-xs text-destructive">{s.status_reason}</p>
              </div>
            </TooltipTrigger>
            <TooltipContent className="max-w-md">{s.status_reason}</TooltipContent>
          </Tooltip>
        )
      },
    },
    {
      id: "url",
      accessorFn: (s) => s.url,
      header: "URL",
      cell: ({ row }) =>
        row.original.phase === "Ready" ? (
          <a
            href={row.original.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 font-mono text-xs hover:underline"
            onClick={(e) => e.stopPropagation()}
          >
            {row.original.url.replace(/^https?:\/\//, "")}
            <ExternalLinkIcon className="size-3" />
          </a>
        ) : (
          <span className="font-mono text-xs text-muted-foreground">
            {row.original.url.replace(/^https?:\/\//, "")}
          </span>
        ),
    },
    {
      id: "created",
      accessorFn: (s) => s.created_at,
      header: "Created",
      cell: ({ row }) => (
        <span title={formatDateTime(row.original.created_at)}>{formatRelative(row.original.created_at)}</span>
      ),
    },
    {
      id: "created_by",
      accessorFn: (s) => s.created_by_name ?? s.created_by_email ?? "",
      header: "Created by",
      cell: ({ row }) => row.original.created_by_name ?? row.original.created_by_email ?? "—",
    },
    {
      id: "actions",
      enableSorting: false,
      enableHiding: false,
      cell: ({ row }) => {
        const s = row.original
        return (
          <div className="flex justify-end" onClick={(e) => e.stopPropagation()}>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${s.name}`}>
                  <MoreHorizontalIcon />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem asChild>
                  <Link to={`/stores/${s.id}`}>View details</Link>
                </DropdownMenuItem>
                {s.phase === "Ready" && (
                  <>
                    <DropdownMenuItem asChild>
                      <a href={s.url} target="_blank" rel="noreferrer">Open storefront</a>
                    </DropdownMenuItem>
                    <DropdownMenuItem asChild>
                      <a href={s.admin_url} target="_blank" rel="noreferrer">Open wp-admin</a>
                    </DropdownMenuItem>
                  </>
                )}
                {canManage(s) && s.phase !== "Deleting" && (
                  <>
                    <DropdownMenuSeparator />
                    {s.phase === "Failed" && (
                      <DropdownMenuItem onClick={() => runRetry(s)}>Retry</DropdownMenuItem>
                    )}
                    <DropdownMenuItem variant="destructive" onClick={() => setDeleting(s)}>
                      Delete store
                    </DropdownMenuItem>
                  </>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        )
      },
    },
  ]

  if (settings.data && !enabled) {
    return (
      <PageBody>
        <PageHeader title="Stores" />
        <EmptyState
          icon={<StoreIcon />}
          title="Stores are not enabled"
          description="This platform does not provision WooCommerce stores (STORES_ENABLED is off)."
        />
      </PageBody>
    )
  }

  return (
    <PageBody>
      <PageHeader
        title="Stores"
        description={
          settings.data
            ? `WooCommerce stores of this team, each with its own namespace, database and MCP server. You can have ${settings.data.max_per_user}; you have ${mine}.`
            : "WooCommerce stores of this team, each with its own namespace, database and MCP server."
        }
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => void stores.refetch()}>
              <RefreshCwIcon /> Refresh
            </Button>
            <Button size="sm" disabled={!enabled || atCap} onClick={() => setCreating(true)}>
              <PlusIcon /> Create store
            </Button>
          </>
        }
      />
      <DataTable
        columns={columns}
        data={stores.data}
        isLoading={settings.isLoading || stores.isLoading}
        error={settings.error ?? stores.error}
        onRetry={() => void stores.refetch()}
        searchPlaceholder="Search stores…"
        getRowId={(s) => s.id}
        onRowClick={(s) => navigate(`/stores/${s.id}`)}
        emptyTitle="No stores yet"
        emptyDescription="Create a store; it is ready in a few minutes and its MCP server is registered for this team automatically."
        emptyAction={
          <Button size="sm" disabled={!enabled || atCap} onClick={() => setCreating(true)}>
            <PlusIcon /> Create store
          </Button>
        }
      />

      <CreateStoreDialog
        open={creating}
        onOpenChange={setCreating}
        teamId={teamId}
        baseDomain={settings.data?.base_domain}
        onCreated={(s) => s.admin_password && setCreated(s)}
      />
      <AdminPasswordDialog store={created} onClose={() => setCreated(null)} />
      <DeleteStoreDialog store={deleting} teamId={teamId} onOpenChange={(o) => !o && setDeleting(null)} />
    </PageBody>
  )
}
