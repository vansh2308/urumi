import type { ReactNode } from "react"

import { CopyButton } from "@/components/common/copy-button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { mcpEndpointUrl } from "@/features/mcp-endpoint/endpoint"
import { useOAuthConfig } from "@/hooks/queries/use-oauth"

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

/** How to point Claude Code, Cursor or the MCP Inspector at every enabled server at once: with
 *  OAuth (browser sign-in, when the gateway offers it) or with an API key. */
export function ConnectMcpCard({ children }: { children?: ReactNode }) {
  const oauth = useOAuthConfig()
  // OAuth clients must use the gateway's own public URL (the protected resource it advertises);
  // key-based clients may also go through the dashboard's proxy.
  const url = (oauth.data?.enabled && oauth.data.mcp_url) || mcpEndpointUrl()
  const withKey = `claude mcp add --transport http urumi ${url} \\\n  --header "Authorization: Bearer <your urk_ key>"`
  const cursorWithKey = JSON.stringify(
    { mcpServers: { urumi: { url, headers: { Authorization: "Bearer <your urk_ key>" } } } },
    null,
    2
  )
  const withOAuth = `claude mcp add --transport http urumi ${url}\n# then run /mcp in Claude Code and choose Authenticate`
  const cursorWithOAuth = JSON.stringify({ mcpServers: { urumi: { url } } }, null, 2)
  return (
    <Card>
      <CardHeader>
        <CardTitle>Connect an MCP client</CardTitle>
        <CardDescription>
          One URL gives a client the tools of every server your team enabled, named{" "}
          <code>server__tool</code>. Servers that are down are left out until they recover.
          {oauth.data?.enabled
            ? " Clients that support OAuth need only the URL: you approve them in the browser and they get a short-lived key for one team."
            : " Use it with one of your API keys."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Snippet label="Endpoint" value={url} />
        {oauth.data?.enabled && (
          <div className="grid gap-3 lg:grid-cols-2">
            <Snippet label="Claude Code (OAuth)" value={withOAuth} />
            <Snippet label="Cursor (OAuth, mcp.json)" value={cursorWithOAuth} />
          </div>
        )}
        <div className="grid gap-3 lg:grid-cols-2">
          <Snippet label="Claude Code (API key)" value={withKey} />
          <Snippet label="Cursor (API key, mcp.json)" value={cursorWithKey} />
        </div>
        {children}
      </CardContent>
    </Card>
  )
}
