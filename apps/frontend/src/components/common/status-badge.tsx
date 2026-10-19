import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

type Tone = "positive" | "negative" | "warning" | "neutral"

const TONES: Record<string, Tone> = {
  success: "positive",
  active: "positive",
  completed: "positive",
  ready: "positive",
  enabled: "positive",
  accepted: "positive",
  ok: "positive",
  pending: "warning",
  running: "warning",
  deploying: "warning",
  unknown: "warning",
  step_limit: "warning",
  tool_limit: "warning",
  token_limit: "warning",
  skipped: "warning",
  cancelled: "neutral",
  expired: "neutral",
  revoked: "neutral",
  disabled: "neutral",
  stopped: "neutral",
  denied: "negative",
  failure: "negative",
  failed: "negative",
  timeout: "negative",
  error: "negative",
  not_ready: "negative",
  unreachable: "negative",
  missing: "negative",
}

const DOT: Record<Tone, string> = {
  positive: "bg-emerald-500",
  negative: "bg-destructive",
  warning: "bg-amber-500",
  neutral: "bg-muted-foreground/50",
}

export function humanize(status: string): string {
  const text = status.replace(/_/g, " ")
  return text.charAt(0).toUpperCase() + text.slice(1)
}

export function toneOf(status: string): Tone {
  return TONES[status] ?? (status.endsWith("error") ? "negative" : "neutral")
}

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const tone = toneOf(status)
  return (
    <Badge variant="outline" className="gap-1.5 font-normal">
      <span className={cn("size-1.5 rounded-full", DOT[tone])} aria-hidden />
      {label ?? humanize(status)}
    </Badge>
  )
}
