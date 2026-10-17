import { KeyRoundIcon } from "lucide-react"
import { useState } from "react"
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts"

import { PageHeader } from "@/components/common/page-header"
import { EmptyState, ErrorAlert } from "@/components/common/query-state"
import { StatCard } from "@/components/common/stat-card"
import { DataTable } from "@/components/data-table/data-table"
import { PageBody } from "@/components/layout/app-shell"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ToolKeyDialog } from "@/features/tool-key/tool-key-dialog"
import { ToolKeyStatus } from "@/features/tool-key/tool-key-status"
import { useToolKey } from "@/features/tool-key/use-tool-key"
import { useAgentUsage } from "@/hooks/queries/use-agent"
import { useTeamId, useWorkspace } from "@/hooks/queries/use-session"
import { useSearchState } from "@/hooks/use-search-state"
import type { AgentUsageSummary } from "@/lib/api/types"
import { formatMs, formatNumber, formatPercent } from "@/lib/format"

type ToolUsage = AgentUsageSummary["tools"][number]

const statusConfig = { runs: { label: "Runs", color: "var(--chart-2)" } } satisfies ChartConfig

export default function AgentMetricsPage() {
  const teamId = useTeamId()
  const { isTeamAdmin } = useWorkspace()
  const { toolKey } = useToolKey(teamId)
  const { get, set } = useSearchState()
  const scope = isTeamAdmin && get("scope") === "team" ? "team" : "me"
  const days = Number(get("days") ?? 7)
  const usage = useAgentUsage(toolKey, scope, days)
  const [keyDialog, setKeyDialog] = useState(false)
  const d = usage.data

  const completed = d?.runs_by_status.completed ?? 0
  const statusData = Object.entries(d?.runs_by_status ?? {}).map(([status, runs]) => ({
    status: status.replace(/_/g, " "),
    runs,
  }))
  const outcomeData = Object.entries(d?.tool_calls_by_outcome ?? {}).map(([status, runs]) => ({
    status: status.replace(/_/g, " "),
    runs,
  }))

  return (
    <PageBody>
      <PageHeader
        title="Agent metrics"
        description="Latency, tokens and tool outcomes from the AI agent's recorded runs."
        actions={
          <>
            {isTeamAdmin && (
              <Tabs value={scope} onValueChange={(v) => set({ scope: v === "me" ? null : v })}>
                <TabsList>
                  <TabsTrigger value="me">Mine</TabsTrigger>
                  <TabsTrigger value="team">Team</TabsTrigger>
                </TabsList>
              </Tabs>
            )}
            <Select value={String(days)} onValueChange={(v) => set({ days: v === "7" ? null : v })}>
              <SelectTrigger size="sm" className="w-36" aria-label="Period">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[1, 7, 30, 90].map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    Last {n === 1 ? "24 hours" : `${n} days`}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <ToolKeyStatus teamId={teamId} />
          </>
        }
      />

      {!toolKey ? (
        <EmptyState
          icon={<KeyRoundIcon />}
          title="Connect an API key"
          description="The agent's metrics API authenticates with a platform API key."
          action={<Button onClick={() => setKeyDialog(true)}>Connect API key</Button>}
        />
      ) : usage.error ? (
        <ErrorAlert error={usage.error} title="Could not load agent metrics" onRetry={() => void usage.refetch()} />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard
              label="Runs"
              isLoading={usage.isLoading}
              value={formatNumber(d?.runs)}
              hint={`${formatPercent(completed, d?.runs ?? 0)} completed`}
            />
            <StatCard
              label="Response latency"
              isLoading={usage.isLoading}
              value={formatMs(d?.p50_latency_ms)}
              hint={`p50 · p95 ${formatMs(d?.p95_latency_ms)} · avg ${formatMs(d?.avg_latency_ms)}`}
            />
            <StatCard
              label="First token"
              isLoading={usage.isLoading}
              value={formatMs(d?.avg_first_token_ms)}
              hint={`avg model call ${formatMs(d?.avg_llm_latency_ms)}`}
            />
            <StatCard
              label="Tokens"
              isLoading={usage.isLoading}
              value={formatNumber((d?.input_tokens ?? 0) + (d?.output_tokens ?? 0))}
              hint={`${formatNumber(d?.input_tokens)} in · ${formatNumber(d?.output_tokens)} out · ${d?.avg_steps?.toFixed(1) ?? "—"} steps/run`}
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            {[
              { title: "Runs by status", description: "How each chat request ended", data: statusData },
              {
                title: "Tool calls by outcome",
                description: `${formatNumber(d?.tool_calls)} calls made by the agent`,
                data: outcomeData,
              },
            ].map((chart) => (
              <Card key={chart.title}>
                <CardHeader>
                  <CardTitle>{chart.title}</CardTitle>
                  <CardDescription>{chart.description}</CardDescription>
                </CardHeader>
                <CardContent>
                  {chart.data.length ? (
                    <ChartContainer config={statusConfig} className="aspect-auto h-56 w-full">
                      <BarChart data={chart.data} layout="vertical" margin={{ left: 24, right: 8 }}>
                        <CartesianGrid horizontal={false} />
                        <XAxis type="number" allowDecimals={false} tickLine={false} axisLine={false} />
                        <YAxis type="category" dataKey="status" tickLine={false} axisLine={false} width={110} />
                        <ChartTooltip content={<ChartTooltipContent hideLabel />} />
                        <Bar dataKey="runs" fill="var(--color-runs)" radius={4} />
                      </BarChart>
                    </ChartContainer>
                  ) : (
                    <p className="py-10 text-center text-sm text-muted-foreground">No data in this period.</p>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>

          <section className="space-y-3">
            <h2 className="text-base font-medium">Tools used by the agent</h2>
            <DataTable<ToolUsage>
              columns={[
                {
                  id: "tool",
                  accessorFn: (t) => `${t.connector ?? ""}/${t.tool ?? ""}`,
                  header: "Tool",
                  cell: ({ row }) => (
                    <span className="font-mono text-xs">
                      <span className="text-muted-foreground">{row.original.connector ?? "?"}/</span>
                      {row.original.tool ?? "unknown"}
                    </span>
                  ),
                },
                { accessorKey: "calls", header: "Calls" },
                { accessorKey: "failures", header: "Failures" },
                {
                  id: "failure_rate",
                  accessorFn: (t) => (t.calls ? t.failures / t.calls : 0),
                  header: "Failure rate",
                  cell: ({ row }) => formatPercent(row.original.failures, row.original.calls),
                },
                {
                  accessorKey: "avg_latency_ms",
                  header: "Avg latency",
                  cell: ({ row }) => formatMs(row.original.avg_latency_ms),
                },
              ]}
              data={d?.tools}
              isLoading={usage.isLoading}
              getRowId={(t) => `${t.connector}/${t.tool}`}
              emptyTitle="The agent has not called any tools in this period"
            />
          </section>
        </>
      )}
      <ToolKeyDialog open={keyDialog} onOpenChange={setKeyDialog} teamId={teamId} />
    </PageBody>
  )
}
