import type { ColumnDef } from "@tanstack/react-table"

import { CopyableId } from "@/components/common/copy-button"
import { StatusBadge } from "@/components/common/status-badge"
import type { Invocation } from "@/lib/api/types"
import { formatDateTime, formatMs } from "@/lib/format"

export function invocationColumns(nameOf: (id: string | null) => string): ColumnDef<Invocation, unknown>[] {
  return [
    {
      accessorKey: "created_at",
      header: "Time",
      cell: ({ row }) => (
        <span className="whitespace-nowrap">{formatDateTime(row.original.created_at)}</span>
      ),
    },
    {
      id: "tool",
      accessorFn: (r) => `${r.connector_slug}/${r.tool_name}`,
      header: "Tool",
      cell: ({ row }) => (
        <span className="font-mono text-xs">
          <span className="text-muted-foreground">{row.original.connector_slug}/</span>
          {row.original.tool_name}
        </span>
      ),
    },
    {
      id: "user",
      accessorFn: (r) => nameOf(r.user_id),
      header: "User",
    },
    {
      accessorKey: "outcome",
      header: "Outcome",
      cell: ({ row }) => <StatusBadge status={row.original.outcome} />,
    },
    {
      accessorKey: "error_code",
      header: "Error",
      cell: ({ row }) =>
        row.original.error_code ? (
          <span className="font-mono text-xs">{row.original.error_code}</span>
        ) : (
          <span className="text-muted-foreground">—</span>
        ),
    },
    {
      accessorKey: "duration_ms",
      header: "Duration",
      cell: ({ row }) => <span className="tabular-nums">{formatMs(row.original.duration_ms)}</span>,
    },
    { accessorKey: "transport", header: "Transport" },
    {
      accessorKey: "request_id",
      header: "Request ID",
      enableSorting: false,
      cell: ({ row }) =>
        row.original.request_id ? <CopyableId value={row.original.request_id} /> : "—",
    },
  ]
}
