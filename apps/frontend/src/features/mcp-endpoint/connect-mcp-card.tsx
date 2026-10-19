import type { ReactNode } from "react"

import { CopyButton } from "@/components/common/copy-button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { mcpEndpointUrl } from "@/features/mcp-endpoint/endpoint"

function Snippet({ label, value }: { label: string; value: string }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        {label}
        <CopyButton value={value} label={`Copy ${label}`} />
      </div>
      <pre className="overflow-x-auto rounded-md border bg-muted/40 p-3 font-mono text-xs">{value}</pre>
    </div>
  )
}

/** How to point Claude Code, Cursor or the MCP Inspector at every enabled server at once. */
export function ConnectMcpCard({ children }: { children?: ReactNode }) {
  const url = mcpEndpointUrl()
  const claude = `claude mcp add --transport http urumi ${url} \\\n  --header "Authorization: Bearer <your urk_ key>"`
  const cursor = JSON.stringify(
    { mcpServers: { urumi: { url, headers: { Authorization: "Bearer <your urk_ key>" } } } },
    null,
    2
  )
  return (
    <Card>
      <CardHeader>
        <CardTitle>Connect an MCP client</CardTitle>
        <CardDescription>
          One URL and your API key give a client the tools of every server your team enabled, named{" "}
          <code>server__tool</code>. Servers that are down are left out until they recover.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Snippet label="Endpoint" value={url} />
        <div className="grid gap-3 lg:grid-cols-2">
          <Snippet label="Claude Code" value={claude} />
          <Snippet label="Cursor (mcp.json)" value={cursor} />
        </div>
        {children}
      </CardContent>
    </Card>
  )
}
