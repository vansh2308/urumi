import { StatusBadge } from "@/components/common/status-badge"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import type { ConnectorStatus } from "@/lib/api/types"
import { formatMs, formatRelative } from "@/lib/format"

const LABEL = { connected: "Connected", unreachable: "Unreachable", disabled: "Disabled" } as const
const TONE = { connected: "ready", unreachable: "unreachable", disabled: "disabled" } as const

/** Connected / Unreachable / Disabled from the gateway's live ping, with the reason on hover. */
export function HealthBadge({ status }: { status: ConnectorStatus | undefined }) {
  if (!status) return <span className="text-muted-foreground">—</span>
  const detail =
    status.state === "unreachable"
      ? `${status.error_code}: ${status.error_message}`
      : status.state === "connected" && status.latency_ms !== null
        ? `Answered in ${formatMs(status.latency_ms)}`
        : "Disabled in the registry"
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span>
          <StatusBadge status={TONE[status.state]} label={LABEL[status.state]} />
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-80">
        <p>{detail}</p>
        <p className="text-muted-foreground">Checked {formatRelative(status.checked_at)}</p>
      </TooltipContent>
    </Tooltip>
  )
}
