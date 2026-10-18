import { afterEach, expect, it, vi } from "vitest"

import { agent, type RunEvent } from "./agent"

afterEach(() => vi.unstubAllGlobals())

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
}

it("parses the run event stream across chunk boundaries, skipping keep-alives and bad frames", async () => {
  const body = streamOf([
    'event: run.started\r\ndata: {"run_id":"r1","conversation_id":"c1"}\r\n\r\n',
    ": keep-alive\n\n",
    'event: message.delta\ndata: {"step":1,',
    '"text":"Hel"}\n\nevent: message.delta\ndata: {"step":1,"text":"lo"}\n\n',
    "event: tool.result\ndata: {not json}\n\n",
    'event: run.completed\ndata: {"status":"completed"}\n\n',
  ])
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }))
  )

  const events: RunEvent[] = []
  for await (const event of agent.chat("urk_key", "hi", null, new AbortController().signal)) events.push(event)

  expect(events.map((e) => e.type)).toEqual([
    "run.started",
    "message.delta",
    "message.delta",
    "run.completed",
  ])
  const text = events.flatMap((e) => (e.type === "message.delta" ? [e.data.text] : [])).join("")
  expect(text).toBe("Hello")
})

it("throws the refusal before any event when the agent rejects the request", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({ error: { code: "conversation_busy", message: "Busy." } }), {
          status: 409,
          headers: { "Content-Type": "application/json" },
        })
    )
  )
  const run = agent.chat("urk_key", "hi", "c1", new AbortController().signal)
  await expect(run.next()).rejects.toMatchObject({ status: 409, code: "conversation_busy" })
})
