import { ActivityIcon, AlertTriangleIcon, HeartPulseIcon, ServerIcon, WrenchIcon } from "lucide-react"
import { Link } from "react-router"

import { ErrorAlert } from "@/components/common/query-state"
import { PageHeader } from "@/components/common/page-header"
import { StatCard } from "@/components/common/stat-card"
import { StatusBadge } from "@/components/common/status-badge"
import { DataTable } from "@/components/data-table/data-table"
import { PageBody } from "@/components/layout/app-shell"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { CallsChart } from "@/features/usage/calls-chart"
import { invocationColumns } from "@/features/usage/invocation-columns"
import { useInvocations, useServiceHealth, useUsageSummary } from "@/hooks/queries/use-activity"
import { useAllTools } from "@/hooks/queries/use-connectors"
import { useMemberNames } from "@/hooks/queries/use-member-names"
import { useTeamId, useWorkspace } from "@/hooks/queries/use-session"
import { daysAgo, formatMs, formatNumber, formatPercent } from "@/lib/format"

const DAYS = 14
// Computed once per page load so query keys stay stable between renders.
const SINCE = daysAgo(DAYS)

export default function OverviewPage() {
  const teamId = useTeamId()
  const { membership, isTeamAdmin } = useWorkspace()
  const health = useServiceHealth()
  const catalog = useAllTools(teamId)
  const byDay = useUsageSummary(teamId, { group_by: "day", since: SINCE })
  const byTool = useUsageSummary(teamId, { group_by: "tool", since: SINCE })
  const recent = useInvocations(teamId, { limit: 50 })
  const nameOf = useMemberNames(teamId)

  const totals = (byDay.data?.rows ?? []).reduce(
    (acc, r) => ({ calls: acc.calls + r.calls, errors: acc.errors + r.errors, denied: acc.denied + r.denied }),
    { calls: 0, errors: 0, denied: 0 }
  )
  const recentErrors = (recent.data?.items ?? []).filter((i) => i.outcome !== "success").slice(0, 5)
  const connectors = catalog.connectors.data ?? []
  const scope = isTeamAdmin ? "the whole team" : "your own activity"

  return (
    <PageBody>
      <PageHeader
        title="Overview"
        description={`${membership?.team_name} · last ${DAYS} days of ${scope}`}
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="System health"
          icon={<HeartPulseIcon />}
          isLoading={health.isLoading}
          value={
            health.data?.gateway === "ready" && health.data.agent === "ready" ? "Ready" : "Degraded"
          }
          hint={
            health.data && (
              <span className="flex flex-wrap gap-1.5">
                <StatusBadge status={health.data.gateway} label={`Gateway ${health.data.gateway.replace("_", " ")}`} />
                <StatusBadge status={health.data.agent} label={`Agent ${health.data.agent.replace("_", " ")}`} />
              </span>
            )
          }
        />
        <StatCard
          label="MCP servers"
          icon={<ServerIcon />}
          isLoading={catalog.connectors.isLoading}
          value={formatNumber(connectors.length)}
          hint={
            catalog.failed.length
              ? `${catalog.failed.length} unreachable right now`
              : "Enabled for this team"
          }
        />
        <StatCard
          label="Available tools"
          icon={<WrenchIcon />}
          isLoading={catalog.isLoading}
          value={formatNumber(catalog.tools.length)}
          hint="You are permitted to use"
        />
        <StatCard
          label="Tool calls"
          icon={<ActivityIcon />}
          isLoading={byDay.isLoading}
          value={formatNumber(totals.calls)}
          hint={`${formatPercent(totals.errors, totals.calls)} errors · ${formatNumber(totals.denied)} denied`}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHeader>
            <CardTitle>Tool invocations</CardTitle>
            <CardDescription>Calls per day through the gateway</CardDescription>
          </CardHeader>
          <CardContent>
            {byDay.error ? (
              <ErrorAlert error={byDay.error} />
            ) : byDay.isLoading ? (
              <Skeleton className="h-64 w-full" />
            ) : (
              <CallsChart rows={byDay.data?.rows ?? []} days={DAYS} />
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Top tools</CardTitle>
            <CardDescription>By call count, with latency</CardDescription>
            <CardAction>
              <Button variant="ghost" size="sm" asChild>
                <Link to="/usage">Usage</Link>
              </Button>
            </CardAction>
          </CardHeader>
          <CardContent>
            {byTool.error ? (
              <ErrorAlert error={byTool.error} />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Tool</TableHead>
                    <TableHead className="text-right">Calls</TableHead>
                    <TableHead className="text-right">Errors</TableHead>
                    <TableHead className="text-right">p95</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(byTool.data?.rows ?? []).slice(0, 6).map((r) => (
                    <TableRow key={r.key}>
                      <TableCell className="max-w-40 truncate font-mono text-xs">{r.key}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatNumber(r.calls)}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatNumber(r.errors)}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatMs(r.p95_duration_ms)}</TableCell>
                    </TableRow>
                  ))}
                  {!byTool.isLoading && !byTool.data?.rows.length && (
                    <TableRow>
                      <TableCell colSpan={4} className="py-6 text-center text-muted-foreground">
                        No tool calls yet.
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Registered MCP servers</CardTitle>
            <CardAction>
              <Button variant="ghost" size="sm" asChild>
                <Link to="/mcp-servers">View all</Link>
              </Button>
            </CardAction>
          </CardHeader>
          <CardContent className="space-y-2">
            {catalog.connectors.isLoading && <Skeleton className="h-10 w-full" />}
            {connectors.map((c) => {
              const failed = catalog.failed.some((f) => f.connector.slug === c.slug)
              const count = catalog.tools.filter((t) => t.connector.slug === c.slug).length
              return (
                <Link
                  key={c.slug}
                  to={`/mcp-servers/${c.slug}`}
                  className="flex items-center justify-between gap-2 rounded-md border p-2.5 hover:bg-muted/50"
                >
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium">{c.display_name}</div>
                    <div className="font-mono text-xs text-muted-foreground">{c.slug}</div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
                    {count} tools
                    <StatusBadge status={failed ? "unreachable" : "ready"} label={failed ? "Unreachable" : "Reachable"} />
                  </div>
                </Link>
              )
            })}
            {!catalog.connectors.isLoading && !connectors.length && (
              <p className="py-4 text-center text-sm text-muted-foreground">
                No MCP servers are enabled for this team yet.
              </p>
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <AlertTriangleIcon className="size-4" /> Recent errors
            </CardTitle>
            <CardDescription>Latest failed or denied tool calls</CardDescription>
          </CardHeader>
          <CardContent>
            {recent.error ? (
              <ErrorAlert error={recent.error} />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Tool</TableHead>
                    <TableHead>Outcome</TableHead>
                    <TableHead>Error</TableHead>
                    <TableHead>User</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {recentErrors.map((i) => (
                    <TableRow key={i.id}>
                      <TableCell className="font-mono text-xs">{`${i.connector_slug}/${i.tool_name}`}</TableCell>
                      <TableCell>
                        <StatusBadge status={i.outcome} />
                      </TableCell>
                      <TableCell className="font-mono text-xs">{i.error_code ?? "—"}</TableCell>
                      <TableCell>{nameOf(i.user_id)}</TableCell>
                    </TableRow>
                  ))}
                  {!recent.isLoading && !recentErrors.length && (
                    <TableRow>
                      <TableCell colSpan={4} className="py-6 text-center text-muted-foreground">
                        No recent errors.
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-medium">Recent activity</h2>
          <Button variant="ghost" size="sm" asChild>
            <Link to="/usage">All invocations</Link>
          </Button>
        </div>
        <DataTable
          columns={invocationColumns(nameOf)}
          data={recent.data?.items.slice(0, 10)}
          isLoading={recent.isLoading}
          error={recent.error}
          urlKey="recent"
          initialHidden={{ transport: false, request_id: false }}
          emptyTitle="No tool calls yet"
          emptyDescription="Calls made through the gateway (by API keys or the agent) appear here."
        />
      </section>
    </PageBody>
  )
}
