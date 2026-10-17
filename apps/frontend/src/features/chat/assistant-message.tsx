import { BotIcon, CircleAlertIcon, InfoIcon } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Spinner } from "@/components/ui/spinner"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import type { ToolRisk } from "@/features/tools/schema"
import type { Run } from "@/lib/api/types"
import { formatMs, formatNumber } from "@/lib/format"

import { Markdown } from "./markdown"
import { withCallLog, type AssistantTurn } from "./model"
import { ToolActivityItem } from "./tool-activity"

const LIMIT_STATUS: Record<string, string> = {
  step_limit: "Stopped at the step limit",
  tool_limit: "Stopped at the tool call limit",
  token_limit: "Stopped at the token budget",
  timeout: "Timed out",
  cancelled: "Cancelled",
  failed: "Failed",
}

interface Metrics {
  status: string
  latency: number | null
  firstToken: number | null
  tokensIn: number
  tokensOut: number
  steps: number
  model: string | null
}

function metricsOf(turn: AssistantTurn, run: Run | undefined): Metrics | null {
  if (turn.completion) {
    return {
      status: turn.completion.status,
      latency: turn.completion.latency_ms,
      firstToken: turn.completion.first_token_ms,
      tokensIn: turn.completion.usage.input_tokens,
      tokensOut: turn.completion.usage.output_tokens,
      steps: turn.completion.steps,
      model: turn.model,
    }
  }
  if (run) {
    return {
      status: run.status,
      latency: run.latency_ms,
      firstToken: run.first_token_ms,
      tokensIn: run.input_tokens,
      tokensOut: run.output_tokens,
      steps: run.steps,
      model: `${run.provider} · ${run.model}`,
    }
  }
  return null
}

export function AssistantMessage({
  turn,
  run,
  streaming,
  riskOf,
}: {
  turn: AssistantTurn
  run?: Run
  streaming?: boolean
  riskOf: (functionName: string) => ToolRisk | null
}) {
  const metrics = metricsOf(turn, run)
  const tools = withCallLog(turn.tools, run?.tool_call_log)
  const stopped = metrics && metrics.status !== "completed" && metrics.status !== "running"
  const waiting = streaming && !turn.text && !turn.tools.some((t) => t.status === "running")
  return (
    <div className="flex gap-3">
      <div className="flex size-7 shrink-0 items-center justify-center rounded-full border bg-background">
        <BotIcon className="size-4" />
      </div>
      <div className="min-w-0 flex-1 space-y-2 pt-0.5">
        {turn.discovered && streaming && (
          <div className="text-xs text-muted-foreground">
            {turn.discovered.count} tools available
            {turn.discovered.unavailable_connectors.length > 0 &&
              ` · unavailable: ${turn.discovered.unavailable_connectors.join(", ")}`}
          </div>
        )}
        {tools.length > 0 && (
          <div className="space-y-1.5">
            {tools.map((t) => (
              <ToolActivityItem key={t.callId} activity={t} risk={riskOf(t.name)} />
            ))}
          </div>
        )}
        {turn.text && <Markdown text={turn.text} />}
        {waiting && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Spinner className="size-3.5" />
            {turn.runId ? "Thinking…" : "Starting…"}
          </div>
        )}
        {turn.error && (
          <Alert variant="destructive">
            <CircleAlertIcon />
            <AlertTitle>The agent could not finish</AlertTitle>
            <AlertDescription>
              <p>{turn.error.message}</p>
              <p className="font-mono text-xs opacity-80">
                {turn.error.code}
                {turn.error.request_id && ` · request ${turn.error.request_id}`}
              </p>
            </AlertDescription>
          </Alert>
        )}
        {stopped && !turn.error && (
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <InfoIcon className="size-3.5" />
            {LIMIT_STATUS[metrics.status] ?? metrics.status}
          </div>
        )}
        {metrics && !streaming && (
          <Tooltip>
            <TooltipTrigger asChild>
              <div className="inline-flex flex-wrap gap-x-3 text-xs text-muted-foreground tabular-nums">
                <span>{formatMs(metrics.latency)}</span>
                {metrics.firstToken != null && <span>first token {formatMs(metrics.firstToken)}</span>}
                <span>{formatNumber(metrics.tokensIn + metrics.tokensOut)} tokens</span>
                <span>{metrics.steps} step{metrics.steps === 1 ? "" : "s"}</span>
              </div>
            </TooltipTrigger>
            <TooltipContent>
              {metrics.model} · {formatNumber(metrics.tokensIn)} in / {formatNumber(metrics.tokensOut)} out
            </TooltipContent>
          </Tooltip>
        )}
      </div>
    </div>
  )
}
