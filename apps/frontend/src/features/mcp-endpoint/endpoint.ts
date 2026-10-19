import { GATEWAY_URL } from "@/lib/api/http"

/** The gateway's all-servers MCP endpoint as seen from this browser (through the dashboard's
 *  same-origin proxy), so it works wherever the dashboard is served. */
export function mcpEndpointUrl(): string {
  const base = GATEWAY_URL.startsWith("http") ? GATEWAY_URL : `${window.location.origin}${GATEWAY_URL}`
  return `${base.replace(/\/$/, "")}/v1/mcp`
}
