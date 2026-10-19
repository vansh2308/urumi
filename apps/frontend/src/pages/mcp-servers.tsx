import type { ColumnDef } from "@tanstack/react-table"
import { ChevronDownIcon, MoreHorizontalIcon, PlusIcon, RefreshCwIcon } from "lucide-react"
import { useMemo, useState } from "react"
import { Link, useNavigate } from "react-router"
import { toast } from "sonner"

import { CopyButton } from "@/components/common/copy-button"
import { PageHeader } from "@/components/common/page-header"
import { StatusBadge } from "@/components/common/status-badge"
import { DataTable } from "@/components/data-table/data-table"
import { PageBody } from "@/components/layout/app-shell"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { describeAccess } from "@/features/connectors/access"
import { ConnectorFormDialog } from "@/features/connectors/connector-form-dialog"
import { HostedServerDialog } from "@/features/connectors/hosted-server-dialog"
import { TeamPolicyDialog } from "@/features/connectors/team-policy-dialog"
import { useConnectorActions } from "@/features/connectors/use-connector-actions"
import {
  useAllTools,
  useHostedServers,
  useHostedSettings,
  useProbeConnector,
  useRegistry,
} from "@/hooks/queries/use-connectors"
import { useTeamId, useWorkspace } from "@/hooks/queries/use-session"
import { errorMessage } from "@/lib/api/http"
import type { Connector, HostedServer, TeamConnector } from "@/lib/api/types"

export interface ServerRow {
  slug: string
  displayName: string
  description: string
  registry: Connector | null
  hosted: HostedServer | null
  team: TeamConnector | null
  health: "reachable" | "unreachable" | "unknown"
  toolCount: number | null
}

export default function McpServersPage() {
  const teamId = useTeamId()
  const { isTeamAdmin, isPlatformAdmin } = useWorkspace()
  const navigate = useNavigate()
  const registry = useRegistry()
  const hostedSettings = useHostedSettings(isPlatformAdmin)
  const hosted = useHostedServers(isPlatformAdmin && hostedSettings.data?.enabled === true)
  const catalog = useAllTools(teamId)
  const probe = useProbeConnector()
  const actions = useConnectorActions(teamId)
  const [registering, setRegistering] = useState(false)
  const [deploying, setDeploying] = useState(false)
  const [editing, setEditing] = useState<Connector | null>(null)
  const [policyFor, setPolicyFor] = useState<ServerRow | null>(null)

  const rows = useMemo<ServerRow[]>(() => {
    const team = catalog.connectors.data ?? []
    const bySlug = new Map<string, ServerRow>()
    const hostedById = new Map((hosted.data ?? []).map((h) => [h.connector.id, h]))
    for (const c of registry.data ?? []) {
      bySlug.set(c.slug, {
        slug: c.slug,
        displayName: c.display_name,
        description: c.description,
        registry: c,
        hosted: hostedById.get(c.id) ?? null,
        team: null,
        health: "unknown",
        toolCount: null,
      })
    }
    for (const t of team) {
      const row = bySlug.get(t.slug)
      const failed = catalog.failed.some((f) => f.connector.slug === t.slug)
      const tools = catalog.tools.filter((x) => x.connector.slug === t.slug)
      const known = failed || tools.length > 0
      const merged: ServerRow = {
        slug: t.slug,
        displayName: t.display_name,
        description: t.description,
        registry: row?.registry ?? null,
        hosted: row?.hosted ?? null,
        team: t,
        health: failed ? "unreachable" : known ? "reachable" : "unknown",
        toolCount: known && !failed ? tools.length : null,
      }
      bySlug.set(t.slug, merged)
    }
    return [...bySlug.values()]
  }, [registry.data, hosted.data, catalog.connectors.data, catalog.failed, catalog.tools])

  const runProbe = (c: Connector) =>
    probe.mutate(c.id, {
      onSuccess: (p) =>
        toast.success(`${c.display_name} is reachable`, {
          description: `${p.server_name} ${p.server_version} · MCP ${p.protocol_version} · ${p.tools.length} tools`,
        }),
      onError: (e) => toast.error(`${c.display_name} probe failed`, { description: errorMessage(e) }),
    })

  const columns: ColumnDef<ServerRow, unknown>[] = [
    {
      id: "name",
      accessorFn: (r) => `${r.displayName} ${r.slug} ${r.description}`,
      header: "Server",
      cell: ({ row }) => (
        <Link to={`/mcp-servers/${row.original.slug}`} className="block min-w-0 hover:underline">
          <div className="font-medium">{row.original.displayName}</div>
          <div className="font-mono text-xs text-muted-foreground">{row.original.slug}</div>
        </Link>
      ),
    },
    {
      id: "description",
      accessorFn: (r) => r.description,
      header: "Description",
      cell: ({ row }) => (
        <span className="line-clamp-2 max-w-72 text-muted-foreground">{row.original.description || "—"}</span>
      ),
    },
    {
      id: "status",
      accessorFn: (r) => (r.registry ? (r.registry.enabled ? "enabled" : "disabled") : "enabled"),
      header: "Status",
      cell: ({ row }) => {
        const r = row.original
        if (r.registry && !r.registry.enabled) return <StatusBadge status="disabled" />
        return r.team ? (
          <StatusBadge status="enabled" label="Enabled for team" />
        ) : (
          <StatusBadge status="disabled" label="Not enabled for team" />
        )
      },
    },
    {
      id: "health",
      accessorFn: (r) => r.health,
      header: "Health",
      cell: ({ row }) =>
        row.original.health === "unknown" ? (
          <span className="text-muted-foreground">—</span>
        ) : (
          <StatusBadge
            status={row.original.health === "reachable" ? "ready" : "unreachable"}
            label={row.original.health === "reachable" ? "Reachable" : "Unreachable"}
          />
        ),
    },
    {
      id: "access",
      accessorFn: (r) => describeAccess(r.team?.your_access),
      header: "Your access",
    },
    ...(isTeamAdmin
      ? [
          {
            id: "policy",
            accessorFn: (r: ServerRow) => describeAccess(r.team?.team_policy),
            header: "Team policy",
          } satisfies ColumnDef<ServerRow, unknown>,
        ]
      : []),
    ...(isPlatformAdmin
      ? [
          {
            id: "type",
            accessorFn: (r: ServerRow) => r.registry?.kind ?? "remote",
            header: "Type",
            cell: ({ row }) => {
              const h = row.original.hosted
              if (row.original.registry?.kind !== "hosted") return <span className="text-muted-foreground">URL</span>
              return (
                <span className="inline-flex items-center gap-2">
                  Hosted
                  {h && <StatusBadge status={h.status.phase} />}
                </span>
              )
            },
          } satisfies ColumnDef<ServerRow, unknown>,
          {
            id: "endpoint",
            accessorFn: (r: ServerRow) => r.registry?.url ?? "",
            header: "Endpoint",
            cell: ({ row }) =>
              row.original.registry?.url ? (
                <span className="inline-flex items-center gap-1 font-mono text-xs">
                  <span className="max-w-56 truncate">{row.original.registry.url}</span>
                  <CopyButton value={row.original.registry.url} label="Copy endpoint" />
                </span>
              ) : (
                "—"
              ),
          } satisfies ColumnDef<ServerRow, unknown>,
        ]
      : []),
    {
      id: "actions",
      enableSorting: false,
      enableHiding: false,
      cell: ({ row }) => {
        const r = row.original
        const canEnableForTeam = isTeamAdmin && (!r.registry || r.registry.enabled)
        return (
          <div className="flex justify-end" onClick={(e) => e.stopPropagation()}>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${r.displayName}`}>
                  <MoreHorizontalIcon />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem asChild>
                  <Link to={`/mcp-servers/${r.slug}`}>View details</Link>
                </DropdownMenuItem>
                {canEnableForTeam && (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuLabel className="text-xs text-muted-foreground">Team</DropdownMenuLabel>
                    <DropdownMenuItem onClick={() => setPolicyFor(r)}>
                      {r.team ? "Edit team tools" : "Enable for team"}
                    </DropdownMenuItem>
                    {r.team && (
                      <DropdownMenuItem
                        variant="destructive"
                        onClick={() => actions.removeFromTeam(r.slug, r.displayName)}
                      >
                        Remove from team
                      </DropdownMenuItem>
                    )}
                  </>
                )}
                {isPlatformAdmin && r.registry && (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuLabel className="text-xs text-muted-foreground">Platform</DropdownMenuLabel>
                    <DropdownMenuItem onClick={() => runProbe(r.registry!)}>Probe server</DropdownMenuItem>
                    <DropdownMenuItem onClick={() => setEditing(r.registry)}>Edit</DropdownMenuItem>
                    <DropdownMenuItem
                      variant={r.registry.enabled ? "destructive" : "default"}
                      onClick={() => actions.setEnabled(r.registry!, !r.registry!.enabled)}
                    >
                      {r.registry.enabled ? "Disable" : "Enable"}
                    </DropdownMenuItem>
                    <DropdownMenuItem variant="destructive" onClick={() => actions.deleteServer(r.registry!)}>
                      Delete server
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

  return (
    <PageBody>
      <PageHeader
        title="MCP servers"
        description="Upstream MCP servers registered with the gateway and their availability to this team."
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                void registry.refetch()
                catalog.refetch()
              }}
            >
              <RefreshCwIcon /> Refresh
            </Button>
            {isPlatformAdmin && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button size="sm">
                    <PlusIcon /> Add server <ChevronDownIcon />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={() => setRegistering(true)}>Register by URL</DropdownMenuItem>
                  <DropdownMenuItem
                    disabled={!hostedSettings.data?.enabled}
                    onClick={() => setDeploying(true)}
                  >
                    Deploy from container image
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </>
        }
      />
      <DataTable
        columns={columns}
        data={rows}
        isLoading={registry.isLoading || catalog.connectors.isLoading}
        error={registry.error ?? catalog.connectors.error}
        onRetry={() => void registry.refetch()}
        searchPlaceholder="Search servers…"
        getRowId={(r) => r.slug}
        onRowClick={(r) => navigate(`/mcp-servers/${r.slug}`)}
        initialHidden={{ description: false }}
        emptyTitle="No MCP servers registered"
        emptyDescription={
          isPlatformAdmin
            ? "Register the WooCommerce MCP server to get started."
            : "A platform admin needs to register an MCP server first."
        }
      />

      {isPlatformAdmin && (
        <>
          <ConnectorFormDialog open={registering} onOpenChange={setRegistering} />
          <HostedServerDialog
            open={deploying}
            onOpenChange={setDeploying}
            onDeployed={(h) => navigate(`/mcp-servers/${h.connector.slug}`)}
          />
          <ConnectorFormDialog
            open={editing !== null}
            onOpenChange={(o) => !o && setEditing(null)}
            connector={editing ?? undefined}
          />
        </>
      )}
      {policyFor && (
        <TeamPolicyDialog
          open
          onOpenChange={(o) => !o && setPolicyFor(null)}
          teamId={teamId}
          slug={policyFor.slug}
          connectorId={policyFor.registry?.id}
          displayName={policyFor.displayName}
          current={policyFor.team?.team_policy ?? null}
        />
      )}
      {actions.dialogs}
    </PageBody>
  )
}
