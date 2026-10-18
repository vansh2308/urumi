import { describe, expect, it } from "vitest"

import type { ConversationMessage } from "@/lib/api/types"

import { itemsFromHistory, splitFunctionName, statusFromResult } from "./model"

const base = { created_at: "2026-10-18T00:00:00Z", tool_call_id: null, tool_name: null, tool_result: null }

function message(partial: Partial<ConversationMessage> & Pick<ConversationMessage, "id" | "role">) {
  return { run_id: "run-1", content: null, tool_calls: null, ...base, ...partial } as ConversationMessage
}

describe("tool status", () => {
  it("is success only when the backend says ok", () => {
    expect(statusFromResult("success", true)).toBe("success")
    expect(statusFromResult("success", false)).toBe("failed")
    expect(statusFromResult("skipped", false)).toBe("skipped")
  })

  it("rebuilds turns from history without inventing success", () => {
    const items = itemsFromHistory([
      message({ id: "m1", role: "user", content: "Do it", run_id: null }),
      message({
        id: "m2",
        role: "assistant",
        tool_calls: [
          { id: "c1", name: "woocommerce__list_products", arguments: {} },
          { id: "c2", name: "woocommerce__update_order_status", arguments: { order_id: 7 } },
          { id: "c3", name: "woocommerce__get_order", arguments: { order_id: 7 } },
        ],
      }),
      message({ id: "m3", role: "tool", tool_call_id: "c1", tool_result: { ok: true, result: [] } }),
      message({
        id: "m4",
        role: "tool",
        tool_call_id: "c2",
        tool_result: { ok: false, error: { code: "forbidden", message: "No." } },
      }),
      message({ id: "m5", role: "assistant", content: "Listed products; the update was denied." }),
    ])

    expect(items.map((i) => i.kind)).toEqual(["user", "assistant"])
    const turn = items[1].kind === "assistant" ? items[1].turn : null
    expect(turn?.tools.map((t) => [t.callId, t.status, t.errorCode])).toEqual([
      ["c1", "success", null],
      ["c2", "failed", "forbidden"],
      ["c3", "failed", null], // no result recorded: it never ran
    ])
    expect(turn?.text).toBe("Listed products; the update was denied.")
  })
})

it("splits agent function names into connector and tool", () => {
  expect(splitFunctionName("woocommerce__list_products")).toEqual({
    connector: "woocommerce",
    tool: "list_products",
  })
  expect(splitFunctionName("plain")).toEqual({ connector: null, tool: "plain" })
})
