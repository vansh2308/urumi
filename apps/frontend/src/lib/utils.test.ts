import { afterEach, describe, expect, it, vi } from "vitest"

import { randomId } from "./utils"

describe("randomId", () => {
  afterEach(() => vi.unstubAllGlobals())

  it("returns distinct v4 UUIDs without crypto.randomUUID (plain-HTTP pages)", () => {
    const real = globalThis.crypto
    vi.stubGlobal("crypto", { getRandomValues: real.getRandomValues.bind(real) })
    const a = randomId()
    expect(a).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
    expect(randomId()).not.toBe(a)
  })
})
