import { TriangleAlertIcon, WrenchIcon } from "lucide-react"

import { ErrorAlert } from "@/components/common/query-state"
import { Button } from "@/components/ui/button"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { useAgentTools } from "@/hooks/queries/use-agent"
import type { ToolKey } from "@/lib/auth/store"

/** The tools the agent would offer the model for this key (GET /v1/tools on the agent). */
export function ToolsPopover({ toolKey }: { toolKey: ToolKey | null }) {
  const tools = useAgentTools(toolKey)
  const count = tools.data?.tools.length
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" disabled={!toolKey}>
          <WrenchIcon />
          {count === undefined ? "Tools" : `${count} tools`}
          {tools.data?.unavailable_connectors.length ? <TriangleAlertIcon className="text-amber-500" /> : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-96 p-0">
        <div className="border-b p-3">
          <div className="text-sm font-medium">Tools available to the agent</div>
          <div className="text-xs text-muted-foreground">
            Discovered through the gateway with your API key, as offered to the model.
          </div>
        </div>
        <ScrollArea className="max-h-80 overflow-y-scroll pb-2">
          <div className="space-y-2 p-3">
            {tools.isLoading && <Skeleton className="h-16 w-full" />}
            {tools.error && <ErrorAlert error={tools.error} title="Could not discover tools" />}
            {tools.data?.unavailable_connectors.map((c) => (
              <div key={c} className="text-xs text-amber-600 dark:text-amber-500">
                {c} is unavailable right now; its tools are not offered.
              </div>
            ))}
            {tools.data?.tools.map((t) => (
              <div key={t.name} className="space-y-0.5">
                <div className="font-mono text-xs">
                  <span className="text-muted-foreground">{t.connector}/</span>
                  {t.tool}
                </div>
                <div className="line-clamp-2 text-xs text-muted-foreground">{t.description}</div>
              </div>
            ))}
            {tools.data && !tools.data.tools.length && (
              <p className="text-sm text-muted-foreground">
                No tools. Ask a team admin to grant you access to an MCP server.
              </p>
            )}
          </div>
        </ScrollArea>
      </PopoverContent>
    </Popover>
  )
}
