import type { ColumnDef } from "@tanstack/react-table"
import { ArrowLeftIcon, PencilIcon, PlugZapIcon, PowerIcon, RefreshCwIcon, Trash2Icon } from "lucide-react"
import { useState, type ReactNode } from "react"
import { Link, useNavigate, useParams } from "react-router"

import { CopyButton } from "@/components/common/copy-button"
import { PageHeader } from "@/components/common/page-header"
import { ErrorAlert } from "@/components/common/query-state"
import { StatusBadge } from "@/components/common/status-badge"
import { DataTable } from "@/components/data-table/data-table"
import { PageBody } from "@/components/layout/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { describeAccess } from "@/features/connectors/access"
import { ConnectorFormDialog } from "@/features/connectors/connector-form-dialog"
import { HostedDeploymentCard } from "@/features/connectors/hosted-deployment-card"
import { TeamPolicyDialog } from "@/features/connectors/team-policy-dialog"
import { useConnectorActions } from "@/features/connectors/use-connector-actions"
import { riskOf } from "@/features/tools/schema"
import { ToolDetailSheet } from "@/features/tools/tool-detail-sheet"
import { ToolRiskBadge } from "@/features/tools/tool-risk-badge"
import { useUsageSummary } from "@/hooks/queries/use-activity"
import {
  useCatalog,
  useConnectorTools,
  useHostedServers,
  useProbeConnector,
  useRegistry,
  useTeamConnectors,
} from "@/hooks/queries/use-connectors"
import { useTeamId, useWorkspace } from "@/hooks/queries/use-session"
import type { CatalogEntry, McpTool } from "@/lib/api/types"
import { daysAgo, formatDateTime, formatMs, formatNumber, formatPercent } from "@/lib/format"
import { NotFoundPage } from "@/pages/errors"

const SINCE = daysAgo(30)

function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-3 gap-2 py-1.5 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="col-span-2 min-w-0 break-words">{children}</dd>
    </div>
  )
}

export default function McpServerDetailPage() {
  const { serverId = "" } = useParams()
  const teamId = useTeamId()
  const { isTeamAdmin, isPlatformAdmin } = useWorkspace()
  const registry = useRegistry()
  const teamConnectors = useTeamConnectors(teamId)
  const connector = registry.data?.find((c) => c.slug === serverId) ?? null
  const navigate = useNavigate()
  const hostedList = useHostedServers(isPlatformAdmin && connector?.kind === "hosted")
  const hosted = hostedList.data?.find((h) => h.connector.id === connector?.id) ?? null
  const team = teamConnectors.data?.find((c) => c.slug === serverId) ?? null
  const hasAccess = Boolean(team && (team.your_access.allow_all_tools || team.your_access.tools.length))
  const tools = useConnectorTools(teamId, serverId, hasAccess)
  const catalog = useCatalog(teamId, serverId, isTeamAdmin && team !== null)
  const usage = useUsageSummary(teamId, { group_by: "connector", connector: serverId, since: SINCE })
  const probe = useProbeConnector()
  const actions = useConnectorActions(teamId, () => navigate("/mcp-servers"))
  const [editing, setEditing] = useState(false)
  const [policyOpen, setPolicyOpen] = useState(false)
  const [selected, setSelected] = useState<McpTool | null>(null)

  if (registry.isLoading || teamConnectors.isLoading) {
    return (
      <PageBody>
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-48 w-full" />
      </PageBody>
    )
  }
  if (!connector && !team) return <NotFoundPage />

  const name = team?.display_name ?? connector?.display_name ?? serverId
  const description = team?.description ?? connector?.description ?? ""
  const stats = usage.data?.rows[0]
  const health = !hasAccess ? null : tools.isLoading ? "checking" : tools.error ? "unreachable" : "reachable"

  const toolColumns: ColumnDef<McpTool, unknown>[] = [
    {
      id: "name",
      accessorFn: (t) => t.name,
      header: "Tool",
      cell: ({ row }) => (
        <div>
          <div className="font-mono text-xs font-medium">{row.original.name}</div>
          {row.original.title && <div className="text-xs text-muted-foreground">{row.original.title}</div>}
        </div>
      ),
    },
    {
      id: "description",
      accessorFn: (t) => t.description ?? "",
      header: "Description",
      cell: ({ row }) => (
        <span className="line-clamp-2 max-w-md whitespace-normal text-muted-foreground">
          {row.original.description || "—"}
        </span>
      ),
    },
    {
      id: "permission",
      accessorFn: (t) => riskOf(t),
      header: "Permission",
      cell: ({ row }) => <ToolRiskBadge risk={riskOf(row.original)} />,
    },
  ]

  const catalogColumns: ColumnDef<CatalogEntry, unknown>[] = [
    {
      accessorKey: "name",
      header: "Tool",
      cell: ({ row }) => <span className="font-mono text-xs">{row.original.name}</span>,
    },
    {
      accessorKey: "description",
      header: "Description",
      cell: ({ row }) => (
        <span className="line-clamp-2 max-w-md whitespace-normal text-muted-foreground">
          {row.original.description || "—"}
        </span>
      ),
    },
    {
      accessorKey: "allowed_for_team",
      header: "Team policy",
      cell: ({ row }) =>
        row.original.allowed_for_team ? (
          <StatusBadge status="enabled" label="Allowed" />
        ) : (
          <StatusBadge status="disabled" label="Not allowed" />
        ),
    },
  ]

  return (
    <PageBody>
      <Button variant="ghost" size="sm" asChild className="-ml-2">
        <Link to="/mcp-servers">
          <ArrowLeftIcon /> MCP servers
        </Link>
      </Button>
      <PageHeader
        title={name}
        description={description || undefined}
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => void tools.refetch()} disabled={!hasAccess}>
              <RefreshCwIcon /> Check health
            </Button>
            {isTeamAdmin && (!connector || connector.enabled) && (
              <Button variant="outline" size="sm" onClick={() => setPolicyOpen(true)}>
                {team ? "Edit team tools" : "Enable for team"}
              </Button>
            )}
            {isTeamAdmin && team && (
              <Button variant="outline" size="sm" onClick={() => actions.removeFromTeam(serverId, name)}>
                Remove from team
              </Button>
            )}
            {isPlatformAdmin && connector && (
              <>
                <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
                  <PencilIcon /> Edit
                </Button>
                <Button
                  variant={connector.enabled ? "destructive" : "default"}
                  size="sm"
                  onClick={() => actions.setEnabled(connector, !connector.enabled)}
                >
                  <PowerIcon /> {connector.enabled ? "Disable" : "Enable"}
                </Button>
                <Button variant="outline" size="sm" onClick={() => actions.deleteServer(connector)}>
                  <Trash2Icon /> Delete
                </Button>
              </>
            )}
          </>
        }
      />

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Connection</CardTitle>
            <CardDescription>Gateway view of this upstream</CardDescription>
          </CardHeader>
          <CardContent>
            <dl className="divide-y">
              <Meta label="Slug">
                <span className="inline-flex items-center gap-1 font-mono text-xs">
                  {serverId}
                  <CopyButton value={serverId} label="Copy slug" />
                </span>
              </Meta>
              <Meta label="Registry">
                {connector ? (
                  <StatusBadge status={connector.enabled ? "enabled" : "disabled"} />
                ) : (
                  "—"
                )}
              </Meta>
              <Meta label="Team">
                {team ? <StatusBadge status="enabled" label="Enabled" /> : <StatusBadge status="disabled" label="Not enabled" />}
              </Meta>
              <Meta label="Health">
                {health === null ? (
                  <span className="text-muted-foreground">Needs tool access to check</span>
                ) : health === "checking" ? (
                  <StatusBadge status="pending" label="Checking" />
                ) : (
                  <StatusBadge
                    status={health === "reachable" ? "ready" : "unreachable"}
                    label={health === "reachable" ? "Reachable" : "Unreachable"}
                  />
                )}
              </Meta>
              <Meta label="Gateway route">
                <span className="inline-flex items-center gap-1 font-mono text-xs">
                  /v1/mcp/{serverId}
                  <CopyButton value={`/v1/mcp/${serverId}`} label="Copy MCP route" />
                </span>
              </Meta>
            </dl>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Access</CardTitle>
            <CardDescription>Least privilege: team policy, then member grants</CardDescription>
          </CardHeader>
          <CardContent>
            <dl className="divide-y">
              <Meta label="Your access">{describeAccess(team?.your_access)}</Meta>
              {isTeamAdmin && <Meta label="Team policy">{describeAccess(team?.team_policy)}</Meta>}
              {isTeamAdmin && team?.team_policy && !team.team_policy.allow_all_tools && (
                <Meta label="Allowed">
                  <div className="flex flex-wrap gap-1">
                    {team.team_policy.tools.map((t) => (
                      <Badge key={t} variant="outline" className="font-mono">
                        {t}
                      </Badge>
                    ))}
                  </div>
                </Meta>
              )}
            </dl>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Usage (30 days)</CardTitle>
            <CardDescription>Tool calls through the gateway</CardDescription>
          </CardHeader>
          <CardContent>
            <dl className="divide-y">
              <Meta label="Calls">{formatNumber(stats?.calls ?? 0)}</Meta>
              <Meta label="Error rate">{formatPercent(stats?.errors ?? 0, stats?.calls ?? 0)}</Meta>
              <Meta label="Denied">{formatNumber(stats?.denied ?? 0)}</Meta>
              <Meta label="Avg / p95">
                {stats ? `${formatMs(stats.avg_duration_ms)} / ${formatMs(stats.p95_duration_ms)}` : "—"}
              </Meta>
            </dl>
          </CardContent>
        </Card>
      </div>

      {isPlatformAdmin && connector && (
        <Card>
          <CardHeader>
            <CardTitle>Endpoint (platform admin)</CardTitle>
            <CardDescription>Only platform admins see the upstream address and limits.</CardDescription>
            <CardAction>
              <Button size="sm" variant="outline" onClick={() => probe.mutate(connector.id)} disabled={probe.isPending}>
                <PlugZapIcon /> Probe
              </Button>
            </CardAction>
          </CardHeader>
          <CardContent className="space-y-3">
            <dl className="grid gap-x-8 sm:grid-cols-2">
              <Meta label="URL">
                <span className="inline-flex items-center gap-1 font-mono text-xs break-all">
                  {connector.url}
                  {connector.url && <CopyButton value={connector.url} label="Copy URL" />}
                </span>
              </Meta>
              <Meta label="Type">{connector.kind === "hosted" ? "Hosted (platform-managed URL)" : "Registered by URL"}</Meta>
              <Meta label="Credential">
                {connector.credential ? (
                  <span>
                    <span className="font-mono text-xs">{connector.credential.header_name}</span>{" "}
                    <span className="text-muted-foreground">(stored encrypted, never shown)</span>
                  </span>
                ) : (
                  <span className="text-muted-foreground">None</span>
                )}
              </Meta>
              <Meta label="Timeout">{connector.timeout_seconds ? `${connector.timeout_seconds} s` : "Default"}</Meta>
              <Meta label="Max concurrency">{connector.max_concurrency ?? "Default"}</Meta>
              <Meta label="Connector ID">
                <span className="inline-flex items-center gap-1 font-mono text-xs">
                  {connector.id.slice(0, 8)}
                  <CopyButton value={connector.id} label="Copy id" />
                </span>
              </Meta>
              <Meta label="Registered">{formatDateTime(connector.created_at)}</Meta>
              <Meta label="Updated">{formatDateTime(connector.updated_at)}</Meta>
            </dl>
            {probe.error && <ErrorAlert error={probe.error} title="Probe failed" />}
            {probe.data && (
              <div className="rounded-md border p-3 text-sm">
                <StatusBadge status="ready" label="Reachable" />{" "}
                <span className="ml-2">
                  {probe.data.server_name} {probe.data.server_version} · MCP protocol{" "}
                  {probe.data.protocol_version} · {probe.data.tools.length} tools exposed
                </span>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {isPlatformAdmin && hosted && <HostedDeploymentCard server={hosted} />}

      <section className="space-y-3">
        <h2 className="text-base font-medium">Your tools</h2>
        {!hasAccess ? (
          <p className="text-sm text-muted-foreground">
            {team
              ? "You have no tools on this server yet. A team admin can grant you access."
              : "This server is not enabled for your team."}
          </p>
        ) : tools.error ? (
          <ErrorAlert error={tools.error} title="Could not list tools" onRetry={() => void tools.refetch()} />
        ) : (
          <DataTable
            columns={toolColumns}
            data={tools.data}
            isLoading={tools.isLoading}
            getRowId={(t) => t.name}
            onRowClick={setSelected}
            urlKey="tools"
            emptyTitle="No tools"
          />
        )}
      </section>

      {isTeamAdmin && team && (
        <section className="space-y-3">
          <h2 className="text-base font-medium">Full catalog</h2>
          <p className="text-sm text-muted-foreground">
            Every tool the server offers, and whether the team policy allows it.
          </p>
          <DataTable
            columns={catalogColumns}
            data={catalog.data}
            isLoading={catalog.isLoading}
            error={catalog.error}
            onRetry={() => void catalog.refetch()}
            getRowId={(t) => t.name}
            urlKey="catalog"
          />
        </section>
      )}

      <ToolDetailSheet
        tool={selected}
        teamId={teamId}
        connector={{ slug: serverId, display_name: name }}
        onOpenChange={(o) => !o && setSelected(null)}
      />
      {isPlatformAdmin && connector && (
        <ConnectorFormDialog open={editing} onOpenChange={setEditing} connector={connector} />
      )}
      {policyOpen && (
        <TeamPolicyDialog
          open
          onOpenChange={setPolicyOpen}
          teamId={teamId}
          slug={serverId}
          connectorId={connector?.id}
          displayName={name}
          current={team?.team_policy ?? null}
        />
      )}
      {actions.dialogs}
    </PageBody>
  )
}
