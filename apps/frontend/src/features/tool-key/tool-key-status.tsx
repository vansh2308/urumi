import { KeyRoundIcon, RefreshCwIcon } from "lucide-react"
import { toast } from "sonner"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { formatRelative } from "@/lib/format"

import { ToolKeyDialog } from "./tool-key-dialog"
import { useToolKey } from "./use-tool-key"

/** Shows which API key this tab uses for tool calls, with connect/disconnect. */
export function ToolKeyStatus({ teamId }: { teamId: string }) {
  const { toolKey, connecting, renew } = useToolKey(teamId)
  const [open, setOpen] = useState(false)
  return (
    <>
      {toolKey ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm">
              <KeyRoundIcon />
              <span className="font-mono text-xs">{toolKey.prefix}…</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="min-w-56">
            <DropdownMenuLabel className="font-normal">
              <div className="text-sm">{toolKey.name}</div>
              <div className="text-xs text-muted-foreground">
                {toolKey.sessionBound
                  ? `Ends with this login · ${formatRelative(toolKey.expiresAt)}`
                  : toolKey.expiresAt
                    ? `Expires ${formatRelative(toolKey.expiresAt)}`
                    : "Pasted key (this tab only)"}
              </div>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => setOpen(true)}>
              <KeyRoundIcon /> Use a different key
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() =>
                void renew().then(
                  () => toast.success("Issued a new session key."),
                  () => toast.error("Could not issue a session key.")
                )
              }
            >
              <RefreshCwIcon /> Issue a new session key
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ) : (
        <Button variant="outline" size="sm" onClick={() => setOpen(true)} disabled={connecting}>
          <KeyRoundIcon /> {connecting ? "Connecting…" : "Connect API key"}
        </Button>
      )}
      <ToolKeyDialog open={open} onOpenChange={setOpen} teamId={teamId} />
    </>
  )
}
