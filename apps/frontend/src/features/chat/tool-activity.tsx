import {
  CheckCircle2Icon,
  ChevronRightIcon,
  CircleSlashIcon,
  WrenchIcon,
  XCircleIcon,
} from "lucide-react"

import { JsonView } from "@/components/common/json-view"
import { Badge } from "@/components/ui/badge"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Spinner } from "@/components/ui/spinner"
import type { ToolRisk } from "@/features/tools/schema"
import { ToolRiskBadge } from "@/features/tools/tool-risk-badge"
import { formatMs } from "@/lib/format"
import { cn } from "@/lib/utils"

import type { ToolActivity, ToolStatus } from "./model"

const STATUS: Record<ToolStatus, { label: string; icon: React.ReactNode; className: string }> = {
  running: { label: "Running", icon: <Spinner className="size-3.5" />, className: "text-muted-foreground" },
  success: { label: "Succeeded", icon: <CheckCircle2Icon className="size-3.5" />, className: "text-emerald-600 dark:text-emerald-500" },
  failed: { label: "Failed", icon: <XCircleIcon className="size-3.5" />, className: "text-destructive" },
  skipped: { label: "Not run", icon: <CircleSlashIcon className="size-3.5" />, className: "text-muted-foreground" },
}

export function ToolActivityItem({ activity, risk }: { activity: ToolActivity; risk: ToolRisk | null }) {
  const s = STATUS[activity.status]
  const label =
    activity.status === "failed" && activity.outcome && activity.outcome !== "tool_error"
      ? activity.outcome.replace(/_/g, " ")
      : activity.status === "failed" && activity.outcome === "tool_error"
        ? "Tool reported an error"
        : s.label
  const error = activity.result?.error as { code?: string; message?: string } | undefined
  return (
    <Collapsible className="rounded-lg border bg-card">
      <CollapsibleTrigger className="group flex w-full items-center gap-2 px-3 py-2 text-left text-sm">
        <ChevronRightIcon className="size-3.5 shrink-0 text-muted-foreground transition-transform group-data-[state=open]:rotate-90" />
        <WrenchIcon className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="min-w-0 truncate font-mono text-xs">
          {activity.connector && <span className="text-muted-foreground">{activity.connector}/</span>}
          {activity.tool ?? activity.name}
        </span>
        {risk && risk !== "read" && <ToolRiskBadge risk={risk} />}
        <span className={cn("ml-auto flex shrink-0 items-center gap-1.5 text-xs capitalize", s.className)}>
          {s.icon}
          {label}
        </span>
        {activity.latencyMs != null && (
          <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{formatMs(activity.latencyMs)}</span>
        )}
      </CollapsibleTrigger>
      <CollapsibleContent className="space-y-3 border-t px-3 py-3">
        <div className="flex flex-wrap gap-1.5 text-xs">
          {activity.httpStatus != null && <Badge variant="outline">Gateway HTTP {activity.httpStatus}</Badge>}
          {activity.outcome && <Badge variant="outline">Outcome: {activity.outcome}</Badge>}
          {activity.errorCode && <Badge variant="outline" className="font-mono">{activity.errorCode}</Badge>}
        </div>
        <div className="space-y-1">
          <div className="text-xs font-medium text-muted-foreground">Arguments</div>
          <JsonView value={activity.arguments ?? {}} />
        </div>
        {error?.message && (
          <div className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">
            {error.message}
          </div>
        )}
        {activity.result && (
          <div className="space-y-1">
            <div className="text-xs font-medium text-muted-foreground">Result returned to the model</div>
            <JsonView value={activity.result} />
          </div>
        )}
        {activity.status === "running" && (
          <p className="text-xs text-muted-foreground">Waiting for the gateway…</p>
        )}
      </CollapsibleContent>
    </Collapsible>
  )
}
