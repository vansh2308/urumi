import { afterEach, describe, expect, it, vi } from "vitest"

import { ApiError, buildUrl, errorMessage, send, setUnauthorizedHandler, toApiError } from "./http"

const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  })

afterEach(() => {
  vi.unstubAllGlobals()
  setUnauthorizedHandler(() => {})
})

describe("buildUrl", () => {
  it("drops empty query values", () => {
    expect(buildUrl("/api/gateway", "/v1/teams", { limit: 20, cursor: "", owner: null })).toBe(
      "/api/gateway/v1/teams?limit=20"
    )
  })
})

describe("toApiError", () => {
  it("reads the services' error body", async () => {
    const error = await toApiError(
      json(422, {
        error: {
          code: "invalid_input",
          message: "Request validation failed.",
          request_id: "req-1",
          details: [{ loc: ["body", "name"], msg: "Field required" }],
        },
      })
    )
    expect(error).toMatchObject({ status: 422, code: "invalid_input", requestId: "req-1" })
    expect(errorMessage(error)).toBe("Request validation failed. name: Field required")
  })

  it("explains a proxy error page when a service is down", async () => {
    const error = await toApiError(new Response("<html>Bad Gateway</html>", { status: 502 }))
    expect(error.code).toBe("http_error")
    expect(error.message).toMatch(/unreachable/)
  })
})

describe("send", () => {
  it("sends the bearer token and reports a 401 once to the session handler", async () => {
    const fetchMock = vi.fn(async () => json(401, { error: { code: "unauthenticated", message: "No." } }))
    vi.stubGlobal("fetch", fetchMock)
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)

    await expect(send("/api/gateway", "/v1/me", { token: "ugs_abc" })).rejects.toBeInstanceOf(ApiError)

    const init = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1]
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer ugs_abc")
    expect(onUnauthorized).toHaveBeenCalledWith("ugs_abc")
  })

  it("turns a network failure into a readable error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("fetch failed"))))
    await expect(send("/api/agent", "/health/live")).rejects.toMatchObject({ code: "network_error" })
  })
})
