import { StatusBadge } from "@/components/common/status-badge"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useServiceHealth } from "@/hooks/queries/use-activity"

/** Compact readiness of the gateway and agent (their /health/ready probes). */
export function ServiceStatus() {
  const { data } = useServiceHealth()
  if (!data) return null
  const allReady = data.gateway === "ready" && data.agent === "ready"
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="hidden sm:inline-flex">
          <StatusBadge
            status={allReady ? "ready" : "not_ready"}
            label={allReady ? "All systems ready" : "Degraded"}
          />
        </span>
      </TooltipTrigger>
      <TooltipContent>
        Gateway: {data.gateway.replace("_", " ")} · Agent: {data.agent.replace("_", " ")}
      </TooltipContent>
    </Tooltip>
  )
}
