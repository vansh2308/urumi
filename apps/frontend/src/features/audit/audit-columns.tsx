import type { ColumnDef } from "@tanstack/react-table"

import { CopyableId } from "@/components/common/copy-button"
import { StatusBadge } from "@/components/common/status-badge"
import { Button } from "@/components/ui/button"
import type { AuditEvent } from "@/lib/api/types"
import { formatDateTime } from "@/lib/format"

export function auditColumns(
  nameOf: (id: string | null) => string,
  onDetails: (event: AuditEvent) => void
): ColumnDef<AuditEvent, unknown>[] {
  return [
    {
      accessorKey: "created_at",
      header: "Time",
      cell: ({ row }) => <span className="whitespace-nowrap">{formatDateTime(row.original.created_at)}</span>,
    },
    {
      id: "actor",
      accessorFn: (e) => nameOf(e.actor_user_id),
      header: "Actor",
      cell: ({ row }) => (
        <div>
          <div>{nameOf(row.original.actor_user_id)}</div>
          {row.original.actor_api_key_id && (
            <div className="font-mono text-xs text-muted-foreground">
              via key {row.original.actor_api_key_id.slice(0, 8)}
            </div>
          )}
        </div>
      ),
    },
    {
      accessorKey: "action",
      header: "Action",
      cell: ({ row }) => <span className="font-mono text-xs">{row.original.action}</span>,
    },
    {
      id: "resource",
      accessorFn: (e) => `${e.target_type ?? ""} ${e.target_id ?? ""}`,
      header: "Resource",
      cell: ({ row }) =>
        row.original.target_type ? (
          <span className="inline-flex items-center gap-1 text-xs">
            <span>{row.original.target_type}</span>
            {row.original.target_id && <CopyableId value={row.original.target_id} />}
          </span>
        ) : (
          <span className="text-muted-foreground">—</span>
        ),
    },
    {
      accessorKey: "outcome",
      header: "Outcome",
      cell: ({ row }) => <StatusBadge status={row.original.outcome} />,
    },
    {
      accessorKey: "ip",
      header: "IP",
      cell: ({ row }) => <span className="font-mono text-xs">{row.original.ip ?? "—"}</span>,
    },
    {
      accessorKey: "request_id",
      header: "Request ID",
      enableSorting: false,
      cell: ({ row }) => (row.original.request_id ? <CopyableId value={row.original.request_id} /> : "—"),
    },
    {
      id: "details",
      enableSorting: false,
      enableHiding: false,
      cell: ({ row }) =>
        Object.keys(row.original.details ?? {}).length ? (
          <Button variant="ghost" size="sm" onClick={() => onDetails(row.original)}>
            Details
          </Button>
        ) : null,
    },
  ]
}
