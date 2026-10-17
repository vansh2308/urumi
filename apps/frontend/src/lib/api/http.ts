// Minimal fetch wrapper shared by the gateway and agent clients. Both services answer errors as
// {"error": {code, message, request_id, details?, retryable?}}; that becomes an ApiError.

export const GATEWAY_URL = (import.meta.env.VITE_GATEWAY_URL as string | undefined) ?? "/api/gateway"
export const AGENT_URL = (import.meta.env.VITE_AGENT_URL as string | undefined) ?? "/api/agent"

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly requestId: string | null
  readonly retryable: boolean
  readonly details: Array<{ loc: string[]; msg: string }>

  constructor(
    status: number,
    code: string,
    message: string,
    opts: { requestId?: string | null; retryable?: boolean; details?: ApiError["details"] } = {}
  ) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.code = code
    this.requestId = opts.requestId ?? null
    this.retryable = opts.retryable ?? false
    this.details = opts.details ?? []
  }
}

export type Query = Record<string, string | number | boolean | null | undefined>

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE"
  token?: string | null
  query?: Query
  body?: unknown
  signal?: AbortSignal
  headers?: Record<string, string>
}

/** Called when a request with a token answers 401, so the session can be cleared once. */
let onUnauthorized: ((token: string) => void) | null = null
export function setUnauthorizedHandler(handler: (token: string) => void) {
  onUnauthorized = handler
}

export function buildUrl(base: string, path: string, query?: Query): string {
  const url = `${base}${path}`
  if (!query) return url
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value))
  }
  const qs = params.toString()
  return qs ? `${url}?${qs}` : url
}

export async function toApiError(response: Response): Promise<ApiError> {
  const requestId = response.headers.get("X-Request-ID")
  try {
    const body = await response.json()
    const err = body?.error
    if (err && typeof err.code === "string") {
      return new ApiError(response.status, err.code, err.message ?? err.code, {
        requestId: err.request_id ?? requestId,
        retryable: Boolean(err.retryable),
        details: Array.isArray(err.details) ? err.details : [],
      })
    }
  } catch {
    // not JSON (proxy error page, service down)
  }
  const message =
    response.status === 502 || response.status === 504
      ? "The service is unreachable. Is it running?"
      : `Request failed with status ${response.status}.`
  return new ApiError(response.status, "http_error", message, { requestId })
}

export async function send(base: string, path: string, opts: RequestOptions = {}): Promise<Response> {
  const headers: Record<string, string> = { Accept: "application/json", ...opts.headers }
  if (opts.token) headers.Authorization = `Bearer ${opts.token}`
  if (opts.body !== undefined) headers["Content-Type"] = "application/json"
  let response: Response
  try {
    response = await fetch(buildUrl(base, path, opts.query), {
      method: opts.method ?? "GET",
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err
    throw new ApiError(0, "network_error", "Could not reach the server. Check that it is running.")
  }
  if (!response.ok) {
    const error = await toApiError(response)
    if (response.status === 401 && opts.token) onUnauthorized?.(opts.token)
    throw error
  }
  return response
}

export async function request<T>(base: string, path: string, opts: RequestOptions = {}): Promise<T> {
  const response = await send(base, path, opts)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.details.length) {
      return `${error.message} ${error.details.map((d) => `${d.loc.slice(1).join(".")}: ${d.msg}`).join("; ")}`
    }
    return error.message
  }
  if (error instanceof Error) return error.message
  return "Something went wrong."
}
