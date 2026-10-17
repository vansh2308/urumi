import type { ColumnDef } from "@tanstack/react-table"
import { RefreshCwIcon } from "lucide-react"
import { useMemo, useState } from "react"

import { ErrorAlert } from "@/components/common/query-state"
import { PageHeader } from "@/components/common/page-header"
import { DataTable } from "@/components/data-table/data-table"
import { PageBody } from "@/components/layout/app-shell"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { ToolKeyStatus } from "@/features/tool-key/tool-key-status"
import { riskOf, type ToolRisk } from "@/features/tools/schema"
import { ToolDetailSheet } from "@/features/tools/tool-detail-sheet"
import { ToolRiskBadge } from "@/features/tools/tool-risk-badge"
import { useUsageSummary } from "@/hooks/queries/use-activity"
import { useAllTools, type DiscoveredTool } from "@/hooks/queries/use-connectors"
import { useTeamId } from "@/hooks/queries/use-session"
import { useSearchState } from "@/hooks/use-search-state"
import type { SummaryRow } from "@/lib/api/types"
import { daysAgo, formatMs, formatNumber } from "@/lib/format"

const SINCE = daysAgo(30)

interface ToolRow extends DiscoveredTool {
  key: string
  risk: ToolRisk
  usage: SummaryRow | undefined
}

export default function ToolsPage() {
  const teamId = useTeamId()
  const { get, set } = useSearchState()
  const catalog = useAllTools(teamId)
  const usage = useUsageSummary(teamId, { group_by: "tool", since: SINCE })
  const [selected, setSelected] = useState<ToolRow | null>(null)
  const server = get("server") ?? "all"
  const access = get("access") ?? "all"

  const rows = useMemo<ToolRow[]>(() => {
    const byKey = new Map((usage.data?.rows ?? []).map((r) => [r.key, r]))
    return catalog.tools
      .map((t) => {
        const key = `${t.connector.slug}/${t.name}`
        return { ...t, key, risk: riskOf(t), usage: byKey.get(key) }
      })
      .filter((t) => server === "all" || t.connector.slug === server)
      .filter((t) => access === "all" || t.risk === access)
  }, [catalog.tools, usage.data, server, access])

  const columns: ColumnDef<ToolRow, unknown>[] = [
    {
      id: "name",
      accessorFn: (r) => `${r.name} ${r.title ?? ""}`,
      header: "Tool",
      cell: ({ row }) => (
        <div>
          <div className="font-mono text-xs font-medium">{row.original.name}</div>
          {row.original.title && <div className="text-xs text-muted-foreground">{row.original.title}</div>}
        </div>
      ),
    },
    {
      id: "server",
      accessorFn: (r) => r.connector.display_name,
      header: "Server",
    },
    {
      id: "description",
      accessorFn: (r) => r.description ?? "",
      header: "Description",
      cell: ({ row }) => (
        <span className="line-clamp-2 max-w-96 text-muted-foreground whitespace-normal">
          {row.original.description || "—"}
        </span>
      ),
    },
    {
      id: "access",
      accessorFn: (r) => r.risk,
      header: "Permission",
      cell: ({ row }) => <ToolRiskBadge risk={row.original.risk} />,
    },
    {
      id: "calls",
      accessorFn: (r) => r.usage?.calls ?? 0,
      header: "Calls (30d)",
      cell: ({ row }) => <span className="tabular-nums">{formatNumber(row.original.usage?.calls ?? 0)}</span>,
    },
    {
      id: "errors",
      accessorFn: (r) => r.usage?.errors ?? 0,
      header: "Errors",
      cell: ({ row }) => <span className="tabular-nums">{formatNumber(row.original.usage?.errors ?? 0)}</span>,
    },
    {
      id: "p95",
      accessorFn: (r) => r.usage?.p95_duration_ms ?? 0,
      header: "p95 latency",
      cell: ({ row }) => (
        <span className="tabular-nums">{row.original.usage ? formatMs(row.original.usage.p95_duration_ms) : "—"}</span>
      ),
    },
  ]

  const connectors = catalog.connectors.data ?? []
  return (
    <PageBody>
      <PageHeader
        title="Tools"
        description="Every MCP tool you are permitted to use, discovered through the gateway."
        actions={
          <>
            <ToolKeyStatus teamId={teamId} />
            <Button variant="outline" size="sm" onClick={catalog.refetch}>
              <RefreshCwIcon /> Refresh
            </Button>
          </>
        }
      />
      {catalog.failed.map((f) => (
        <ErrorAlert
          key={f.connector.slug}
          error={f.error}
          title={`Could not list tools on ${f.connector.display_name}`}
        />
      ))}
      <DataTable
        columns={columns}
        data={rows}
        isLoading={catalog.isLoading}
        error={catalog.connectors.error}
        onRetry={catalog.refetch}
        getRowId={(r) => r.key}
        onRowClick={setSelected}
        searchPlaceholder="Search tools…"
        initialHidden={{ errors: false }}
        emptyTitle="No tools available"
        emptyDescription="No MCP server is enabled for you yet. A team admin can enable servers and grant you tools."
        toolbar={
          <>
            <Select value={server} onValueChange={(v) => set({ server: v === "all" ? null : v, page: null })}>
              <SelectTrigger size="sm" className="w-44" aria-label="Server">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All servers</SelectItem>
                {connectors.map((c) => (
                  <SelectItem key={c.slug} value={c.slug}>
                    {c.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={access} onValueChange={(v) => set({ access: v === "all" ? null : v, page: null })}>
              <SelectTrigger size="sm" className="w-40" aria-label="Permission">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Any permission</SelectItem>
                <SelectItem value="read">Read only</SelectItem>
                <SelectItem value="write">Write</SelectItem>
                <SelectItem value="destructive">Destructive</SelectItem>
              </SelectContent>
            </Select>
          </>
        }
      />
      <ToolDetailSheet
        tool={selected}
        teamId={teamId}
        connector={selected?.connector ?? { slug: "", display_name: "" }}
        onOpenChange={(o) => !o && setSelected(null)}
      />
    </PageBody>
  )
}
