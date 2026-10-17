// Agent REST API (services/agent). Every call authenticates with a gateway platform API key
// (urk_); the agent verifies it with the gateway and forwards it on each tool call.

import { AGENT_URL, request, send } from "./http"
import type {
  AgentMe,
  AgentTools,
  AgentUsageSummary,
  Conversation,
  ConversationDetail,
  Health,
  Run,
} from "./types"

// --- streamed run events (services/agent/src/agent/services/runner.py) ----------------------------

export interface RunStartedEvent {
  run_id: string
  conversation_id: string
  conversation_created: boolean
  provider: string
  model: string
  request_id: string | null
}
export interface ToolsDiscoveredEvent {
  count: number
  tools: Array<{ name: string; connector: string; tool: string; description: string }>
  unavailable_connectors: string[]
}
export interface LlmRequestEvent {
  step: number
  context_messages: number
  tools_offered: number
}
export interface LlmResponseEvent {
  step: number
  finish_reason: string
  tool_calls: string[]
  input_tokens: number
  output_tokens: number
  latency_ms: number
}
export interface MessageDeltaEvent {
  step: number
  text: string
}
export interface ToolCallEvent {
  step: number
  call_id: string
  name: string
  connector: string | null
  tool: string | null
  arguments: unknown
}
export interface ToolResultEvent {
  step: number
  call_id: string
  name: string
  outcome: string
  ok: boolean
  http_status: number | null
  error_code: string | null
  latency_ms: number
  result: Record<string, unknown> | null
}
export interface MessageCompletedEvent {
  message_id: string
  text: string
}
export interface RunErrorEvent {
  code: string
  message: string
  retryable: boolean
  request_id: string | null
}
export interface RunCompletedEvent {
  run_id: string
  conversation_id: string
  status: string
  error_code: string | null
  steps: number
  tool_calls: number
  tools_available: number
  usage: { input_tokens: number; output_tokens: number }
  latency_ms: number
  first_token_ms: number | null
  llm_latency_ms: number
  tool_latency_ms: number
}

export type RunEvent =
  | { type: "run.started"; data: RunStartedEvent }
  | { type: "tools.discovered"; data: ToolsDiscoveredEvent }
  | { type: "llm.request"; data: LlmRequestEvent }
  | { type: "llm.response"; data: LlmResponseEvent }
  | { type: "message.delta"; data: MessageDeltaEvent }
  | { type: "tool.call"; data: ToolCallEvent }
  | { type: "tool.result"; data: ToolResultEvent }
  | { type: "message.completed"; data: MessageCompletedEvent }
  | { type: "error"; data: RunErrorEvent }
  | { type: "run.completed"; data: RunCompletedEvent }

/** Parses a text/event-stream body into events. Comments (keep-alives) are skipped. */
async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<RunEvent> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n")
      let boundary: number
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        let type = "message"
        const data: string[] = []
        for (const line of block.split("\n")) {
          if (line.startsWith(":")) continue
          if (line.startsWith("event:")) type = line.slice(6).trim()
          else if (line.startsWith("data:")) data.push(line.slice(5).trimStart())
        }
        if (!data.length) continue
        try {
          yield { type, data: JSON.parse(data.join("\n")) } as RunEvent
        } catch {
          // ignore a malformed frame rather than abort the whole run view
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

export const agent = {
  health: {
    live: () => request<Health>(AGENT_URL, "/health/live"),
    ready: () => request<Health>(AGENT_URL, "/health/ready"),
  },
  me: (apiKey: string) => request<AgentMe>(AGENT_URL, "/v1/me", { token: apiKey }),
  tools: (apiKey: string) => request<AgentTools>(AGENT_URL, "/v1/tools", { token: apiKey }),

  conversations: {
    list: (apiKey: string, limit = 50, before?: string) =>
      request<Conversation[]>(AGENT_URL, "/v1/conversations", {
        token: apiKey,
        query: { limit, before },
      }),
    get: (apiKey: string, id: string) =>
      request<ConversationDetail>(AGENT_URL, `/v1/conversations/${id}`, { token: apiKey }),
    runs: (apiKey: string, id: string) =>
      request<Run[]>(AGENT_URL, `/v1/conversations/${id}/runs`, { token: apiKey }),
    delete: (apiKey: string, id: string) =>
      request<void>(AGENT_URL, `/v1/conversations/${id}`, { method: "DELETE", token: apiKey }),
  },

  usage: (apiKey: string, scope: "me" | "team", days: number) =>
    request<AgentUsageSummary>(AGENT_URL, "/v1/usage/summary", {
      token: apiKey,
      query: { scope, days },
    }),

  /**
   * Starts a streamed run. Refusals (auth, validation, rate limit, busy conversation) throw an
   * ApiError before any event; afterwards progress and failures arrive as events. Aborting the
   * signal disconnects, which cancels the run on the server.
   */
  async *chat(
    apiKey: string,
    message: string,
    conversationId: string | null,
    signal: AbortSignal
  ): AsyncGenerator<RunEvent> {
    const response = await send(AGENT_URL, "/v1/chat", {
      method: "POST",
      token: apiKey,
      body: { message, conversation_id: conversationId, stream: true },
      headers: { Accept: "text/event-stream" },
      signal,
    })
    if (!response.body) return
    yield* readEvents(response.body)
  },
}
