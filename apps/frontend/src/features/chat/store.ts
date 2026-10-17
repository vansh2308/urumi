// The live agent run. Kept in a store (not component state) so a streaming answer survives
// navigating between conversations or pages; only one run streams at a time per tab.

import { create } from "zustand"

import { agent, type RunEvent } from "@/lib/api/agent"
import { ApiError } from "@/lib/api/http"
import { queryClient, qk } from "@/lib/query"

import { emptyTurn, splitFunctionName, statusFromResult, type AssistantTurn } from "./model"

export interface LiveRun {
  teamId: string
  keyId: string
  /** null until run.started tells us (new conversation). */
  conversationId: string | null
  startedFromNew: boolean
  userText: string
  turn: AssistantTurn
  status: "starting" | "streaming" | "done" | "failed" | "cancelled"
  /** The request was refused before the run started (auth, validation, busy, rate limit). */
  requestError: unknown
}

interface ChatState {
  live: LiveRun | null
  controller: AbortController | null
  send: (args: {
    teamId: string
    apiKey: string
    keyId: string
    conversationId: string | null
    message: string
    onUnauthorized: () => void
  }) => Promise<void>
  cancel: () => void
  dismiss: () => void
}

function apply(turn: AssistantTurn, event: RunEvent): AssistantTurn {
  switch (event.type) {
    case "run.started":
      return { ...turn, runId: event.data.run_id, model: `${event.data.provider} · ${event.data.model}` }
    case "tools.discovered":
      return { ...turn, discovered: event.data }
    case "llm.response":
      return { ...turn, steps: [...turn.steps, event.data] }
    case "message.delta":
      return { ...turn, text: turn.text + event.data.text }
    case "message.completed":
      return { ...turn, text: event.data.text }
    case "tool.call": {
      const names = splitFunctionName(event.data.name)
      return {
        ...turn,
        // Text streamed before a tool call is the model thinking aloud; keep it, separated.
        text: turn.text && !turn.text.endsWith("\n\n") ? `${turn.text}\n\n` : turn.text,
        tools: [
          ...turn.tools,
          {
            callId: event.data.call_id,
            name: event.data.name,
            connector: event.data.connector ?? names.connector,
            tool: event.data.tool ?? names.tool,
            arguments: event.data.arguments,
            status: "running",
            outcome: null,
            httpStatus: null,
            errorCode: null,
            latencyMs: null,
            result: null,
          },
        ],
      }
    }
    case "tool.result":
      return {
        ...turn,
        tools: turn.tools.map((t) =>
          t.callId === event.data.call_id
            ? {
                ...t,
                status: statusFromResult(event.data.outcome, event.data.ok),
                outcome: event.data.outcome,
                httpStatus: event.data.http_status,
                errorCode: event.data.error_code,
                latencyMs: event.data.latency_ms,
                result: event.data.result,
              }
            : t
        ),
      }
    case "error":
      return { ...turn, error: event.data }
    case "run.completed":
      return { ...turn, completion: event.data }
    default:
      return turn
  }
}

export const useChatStore = create<ChatState>()((set, get) => ({
  live: null,
  controller: null,

  async send({ teamId, apiKey, keyId, conversationId, message, onUnauthorized }) {
    get().controller?.abort()
    const controller = new AbortController()
    set({
      controller,
      live: {
        teamId,
        keyId,
        conversationId,
        startedFromNew: conversationId === null,
        userText: message,
        turn: emptyTurn(),
        status: "starting",
        requestError: null,
      },
    })
    const update = (fn: (live: LiveRun) => LiveRun) => {
      const live = get().live
      if (live && get().controller === controller) set({ live: fn(live) })
    }

    let finalStatus: LiveRun["status"] = "done"
    try {
      for await (const event of agent.chat(apiKey, message, conversationId, controller.signal)) {
        update((live) => ({
          ...live,
          status: "streaming",
          conversationId:
            event.type === "run.started" ? event.data.conversation_id : live.conversationId,
          turn: apply(live.turn, event),
        }))
        if (event.type === "run.started" && event.data.conversation_created) {
          void queryClient.invalidateQueries({ queryKey: qk.agent.conversations(keyId) })
        }
        if (event.type === "error") finalStatus = "failed"
      }
      // The stream ended without a completion event: the connection dropped mid-run.
      if (!get().live?.turn.completion && get().controller === controller) {
        finalStatus = "failed"
        update((live) => ({
          ...live,
          turn: {
            ...live.turn,
            error: live.turn.error ?? {
              code: "stream_interrupted",
              message: "The connection closed before the agent finished. Check the conversation for its final state.",
              retryable: true,
              request_id: null,
            },
          },
        }))
      }
    } catch (error) {
      if (controller.signal.aborted) {
        finalStatus = "cancelled"
      } else {
        finalStatus = "failed"
        if (error instanceof ApiError && error.status === 401) onUnauthorized()
        update((live) =>
          live.turn.runId ? { ...live, turn: { ...live.turn, error: toRunError(error) } } : { ...live, requestError: error }
        )
      }
    }

    update((live) => ({ ...live, status: finalStatus }))
    const live = get().live
    if (get().controller !== controller || !live) return
    set({ controller: null })
    if (live.conversationId && !live.requestError) {
      // Swap the live turn for the persisted one once it has been re-read.
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: qk.agent.conversation(keyId, live.conversationId) }),
        queryClient.invalidateQueries({ queryKey: qk.agent.runs(keyId, live.conversationId) }),
        queryClient.invalidateQueries({ queryKey: qk.agent.conversations(keyId) }),
      ])
      // Keep failures and cancellations on screen: the persisted history has no error event.
      if (finalStatus === "done" && get().live === live) set({ live: null })
    }
  },

  cancel() {
    get().controller?.abort()
  },

  dismiss() {
    if (!get().controller) set({ live: null })
  },
}))

function toRunError(error: unknown) {
  if (error instanceof ApiError) {
    return { code: error.code, message: error.message, retryable: error.retryable, request_id: error.requestId }
  }
  return {
    code: "network_error",
    message: "The connection to the agent was lost.",
    retryable: true,
    request_id: null,
  }
}
