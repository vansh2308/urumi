import type { ColumnDef } from "@tanstack/react-table"

import { DebouncedInput } from "@/components/common/debounced-input"
import { PageHeader } from "@/components/common/page-header"
import { ErrorAlert } from "@/components/common/query-state"
import { StatCard } from "@/components/common/stat-card"
import { DataTable } from "@/components/data-table/data-table"
import { PageBody } from "@/components/layout/app-shell"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { RangeSelect, RANGES, sinceFor } from "@/features/audit/range-select"
import { CallsChart } from "@/features/usage/calls-chart"
import { invocationColumns } from "@/features/usage/invocation-columns"
import { useInvocations, useUsageSummary } from "@/hooks/queries/use-activity"
import { useTeamConnectors } from "@/hooks/queries/use-connectors"
import { useMemberNames } from "@/hooks/queries/use-member-names"
import { useTeamId, useWorkspace } from "@/hooks/queries/use-session"
import { useMembers } from "@/hooks/queries/use-team"
import { useCursorPager } from "@/hooks/use-cursor-pager"
import { useSearchState } from "@/hooks/use-search-state"
import type { SummaryGroup, SummaryRow } from "@/lib/api/types"
import { formatMs, formatNumber, formatPercent } from "@/lib/format"

const GROUPS: Record<SummaryGroup, string> = {
  tool: "Tool",
  connector: "Server",
  user: "Member",
  outcome: "Outcome",
  day: "Day",
}

export default function UsagePage() {
  const teamId = useTeamId()
  const { isTeamAdmin } = useWorkspace()
  const { get, set } = useSearchState()
  const range = get("range") === "all" ? "90d" : (get("range") ?? "30d") // summary spans ≤ 366 days
  const groupBy = (get("group") as SummaryGroup | undefined) ?? "tool"
  const connector = get("server") ?? "all"
  const member = get("member") ?? "all"
  const tool = get("tool") ?? ""
  const outcome = get("outcome") ?? ""
  const connectors = useTeamConnectors(teamId)
  const members = useMembers(teamId, isTeamAdmin)
  const nameOf = useMemberNames(teamId)

  const since = sinceFor(range)
  const common = {
    connector: connector === "all" ? undefined : connector,
    user_id: member === "all" ? undefined : member,
    since,
  }
  const summary = useUsageSummary(teamId, { group_by: groupBy, ...common })
  const daily = useUsageSummary(teamId, { group_by: "day", ...common })
  const invFilters = { ...common, tool: tool || undefined, outcome: outcome || undefined }
  const pager = useCursorPager(JSON.stringify(invFilters))
  const invocations = useInvocations(teamId, { ...invFilters, limit: 50, cursor: pager.cursor })

  const totals = (daily.data?.rows ?? []).reduce(
    (a, r) => ({
      calls: a.calls + r.calls,
      errors: a.errors + r.errors,
      denied: a.denied + r.denied,
      weighted: a.weighted + r.avg_duration_ms * r.calls,
    }),
    { calls: 0, errors: 0, denied: 0, weighted: 0 }
  )

  const summaryColumns: ColumnDef<SummaryRow, unknown>[] = [
    {
      accessorKey: "key",
      header: GROUPS[groupBy],
      cell: ({ row }) => (
        <span className={groupBy === "tool" || groupBy === "connector" ? "font-mono text-xs" : undefined}>
          {groupBy === "user" ? nameOf(row.original.key) : row.original.key}
        </span>
      ),
    },
    { accessorKey: "calls", header: "Calls", cell: ({ row }) => formatNumber(row.original.calls) },
    { accessorKey: "errors", header: "Errors", cell: ({ row }) => formatNumber(row.original.errors) },
    { accessorKey: "denied", header: "Denied", cell: ({ row }) => formatNumber(row.original.denied) },
    {
      id: "error_rate",
      accessorFn: (r) => (r.calls ? r.errors / r.calls : 0),
      header: "Error rate",
      cell: ({ row }) => formatPercent(row.original.errors, row.original.calls),
    },
    { accessorKey: "avg_duration_ms", header: "Avg", cell: ({ row }) => formatMs(row.original.avg_duration_ms) },
    { accessorKey: "p95_duration_ms", header: "p95", cell: ({ row }) => formatMs(row.original.p95_duration_ms) },
  ]

  const days = RANGES[range]?.days ?? 30

  return (
    <PageBody>
      <PageHeader
        title="Usage"
        description={
          isTeamAdmin
            ? "Tool invocations through the gateway across the team."
            : "Your tool invocations through the gateway."
        }
        actions={
          <>
            <Select value={connector} onValueChange={(v) => set({ server: v === "all" ? null : v })}>
              <SelectTrigger size="sm" className="w-40" aria-label="Server">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All servers</SelectItem>
                {connectors.data?.map((c) => (
                  <SelectItem key={c.slug} value={c.slug}>
                    {c.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {isTeamAdmin && (
              <Select value={member} onValueChange={(v) => set({ member: v === "all" ? null : v })}>
                <SelectTrigger size="sm" className="w-40" aria-label="Member">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All members</SelectItem>
                  {members.data?.map((m) => (
                    <SelectItem key={m.user_id} value={m.user_id}>
                      {m.display_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            <RangeSelect value={range} onChange={(v) => set({ range: v === "30d" ? null : v })} />
          </>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Calls" isLoading={daily.isLoading} value={formatNumber(totals.calls)} />
        <StatCard
          label="Error rate"
          isLoading={daily.isLoading}
          value={formatPercent(totals.errors, totals.calls)}
          hint={`${formatNumber(totals.errors)} failed or denied`}
        />
        <StatCard label="Denied" isLoading={daily.isLoading} value={formatNumber(totals.denied)} hint="Refused by policy" />
        <StatCard
          label="Avg latency"
          isLoading={daily.isLoading}
          value={totals.calls ? formatMs(totals.weighted / totals.calls) : "—"}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Calls per day</CardTitle>
          <CardDescription>{RANGES[range]?.label}</CardDescription>
        </CardHeader>
        <CardContent>
          {daily.error ? (
            <ErrorAlert error={daily.error} />
          ) : daily.isLoading ? (
            <Skeleton className="h-64 w-full" />
          ) : (
            <CallsChart rows={daily.data?.rows ?? []} days={days} />
          )}
        </CardContent>
      </Card>

      <section className="space-y-3">
        <h2 className="text-base font-medium">Breakdown</h2>
        <DataTable
          columns={summaryColumns}
          data={summary.data?.rows}
          isLoading={summary.isLoading}
          error={summary.error}
          onRetry={() => void summary.refetch()}
          getRowId={(r) => r.key}
          urlKey="sum"
          emptyTitle="No calls in this period"
          toolbar={
            <Select value={groupBy} onValueChange={(v) => set({ group: v === "tool" ? null : v })}>
              <SelectTrigger size="sm" className="w-40" aria-label="Group by">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(GROUPS) as SummaryGroup[])
                  .filter((g) => g !== "user" || isTeamAdmin)
                  .map((g) => (
                    <SelectItem key={g} value={g}>
                      By {GROUPS[g].toLowerCase()}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          }
        />
      </section>

      <section className="space-y-3">
        <h2 className="text-base font-medium">Invocations</h2>
        <DataTable
          columns={invocationColumns(nameOf)}
          data={invocations.data?.items}
          isLoading={invocations.isLoading}
          error={invocations.error}
          onRetry={() => void invocations.refetch()}
          getRowId={(i) => i.id}
          urlKey="inv"
          initialHidden={{ transport: false, ...(isTeamAdmin ? {} : { user: false }) }}
          emptyTitle="No invocations"
          cursor={{
            hasPrevious: pager.hasPrevious,
            hasNext: Boolean(invocations.data?.next_cursor),
            onPrevious: pager.previous,
            onNext: () => invocations.data?.next_cursor && pager.next(invocations.data.next_cursor),
            pageLabel: `Page ${pager.page}`,
          }}
          toolbar={
            <>
              <DebouncedInput
                className="h-7 w-44"
                placeholder="Tool name"
                aria-label="Tool name"
                value={tool}
                onCommit={(v) => set({ tool: v })}
              />
              <Select value={outcome || "all"} onValueChange={(v) => set({ outcome: v === "all" ? null : v })}>
                <SelectTrigger size="sm" className="w-40" aria-label="Outcome">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Any outcome</SelectItem>
                  {["success", "tool_error", "denied", "upstream_unavailable", "upstream_timeout", "upstream_error", "cancelled"].map((o) => (
                    <SelectItem key={o} value={o}>
                      {o.replace(/_/g, " ")}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </>
          }
        />
      </section>
    </PageBody>
  )
}
