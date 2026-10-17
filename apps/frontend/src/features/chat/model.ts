// View model of a chat: user messages and assistant turns with their tool activity, built either
// from persisted conversation messages or from a live run's events.

import type {
  LlmResponseEvent,
  RunCompletedEvent,
  RunErrorEvent,
  ToolsDiscoveredEvent,
} from "@/lib/api/agent"
import type { ConversationMessage, ToolCallLog } from "@/lib/api/types"

export type ToolStatus = "running" | "success" | "failed" | "skipped"

export interface ToolActivity {
  callId: string
  name: string
  connector: string | null
  tool: string | null
  arguments: unknown
  status: ToolStatus
  outcome: string | null
  httpStatus: number | null
  errorCode: string | null
  latencyMs: number | null
  result: Record<string, unknown> | null
}

export interface AssistantTurn {
  runId: string | null
  text: string
  tools: ToolActivity[]
  steps: LlmResponseEvent[]
  discovered: ToolsDiscoveredEvent | null
  completion: RunCompletedEvent | null
  error: RunErrorEvent | null
  model: string | null
}

export type ChatItem =
  | { kind: "user"; id: string; text: string }
  | { kind: "assistant"; id: string; turn: AssistantTurn }

export function emptyTurn(runId: string | null = null): AssistantTurn {
  return {
    runId,
    text: "",
    tools: [],
    steps: [],
    discovered: null,
    completion: null,
    error: null,
    model: null,
  }
}

/** Status from what the backend recorded: success only when the agent said ok. */
export function statusFromResult(outcome: string, ok: boolean): ToolStatus {
  if (ok) return "success"
  if (outcome === "skipped") return "skipped"
  return "failed"
}

function resultStatus(result: Record<string, unknown> | null): ToolStatus {
  if (!result) return "failed"
  if (result.ok === true) return "success"
  return "failed"
}

function asText(content: string | null): string {
  return content?.trim() ?? ""
}

/** Persisted messages -> chat items. Messages of one run form one assistant turn. */
export function itemsFromHistory(messages: ConversationMessage[]): ChatItem[] {
  const items: ChatItem[] = []
  let turn: AssistantTurn | null = null

  const currentTurn = (msg: ConversationMessage): AssistantTurn => {
    if (turn && (turn.runId === msg.run_id || msg.run_id === null)) return turn
    turn = emptyTurn(msg.run_id)
    items.push({ kind: "assistant", id: msg.id, turn })
    return turn
  }

  for (const msg of messages) {
    if (msg.role === "user") {
      items.push({ kind: "user", id: msg.id, text: msg.content ?? "" })
      turn = null
    } else if (msg.role === "assistant") {
      const t = currentTurn(msg)
      const text = asText(msg.content)
      if (text) t.text = t.text ? `${t.text}\n\n${text}` : text
      for (const call of msg.tool_calls ?? []) {
        const names = splitFunctionName(call.name)
        t.tools.push({
          callId: call.id,
          name: call.name,
          connector: names.connector,
          tool: names.tool,
          arguments: call.arguments,
          status: "failed", // replaced by the matching tool message; none means it never ran
          outcome: null,
          httpStatus: null,
          errorCode: null,
          latencyMs: null,
          result: null,
        })
      }
    } else if (msg.role === "tool") {
      const t = currentTurn(msg)
      const activity = t.tools.find((a) => a.callId === msg.tool_call_id)
      if (activity) {
        activity.result = msg.tool_result
        activity.status = resultStatus(msg.tool_result)
        const err = msg.tool_result?.error as { code?: string } | undefined
        activity.errorCode = err?.code ?? null
      }
    }
  }
  return items
}

/** "woocommerce__list_products" -> { connector, tool } as the agent names functions. */
export function splitFunctionName(name: string): { connector: string | null; tool: string } {
  const i = name.indexOf("__")
  return i > 0 ? { connector: name.slice(0, i), tool: name.slice(i + 2) } : { connector: null, tool: name }
}

/** Adds what the run's tool call log recorded (outcome, gateway status, latency) to history. */
export function withCallLog(tools: ToolActivity[], log: ToolCallLog[] | undefined): ToolActivity[] {
  if (!log?.length) return tools
  const byId = new Map(log.map((l) => [l.call_id, l]))
  return tools.map((t) => {
    const l = byId.get(t.callId)
    if (!l) return t
    return {
      ...t,
      connector: l.connector_slug ?? t.connector,
      tool: l.tool_name ?? t.tool,
      outcome: l.outcome,
      status: statusFromResult(l.outcome, l.outcome === "success"),
      httpStatus: l.http_status,
      errorCode: l.error_code ?? t.errorCode,
      latencyMs: l.latency_ms,
    }
  })
}
