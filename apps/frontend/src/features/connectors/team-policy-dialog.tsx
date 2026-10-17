import { useEffect, useState } from "react"
import { toast } from "sonner"

import { ErrorAlert } from "@/components/common/query-state"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { useCatalog, useProbeConnector, useSetTeamPolicy } from "@/hooks/queries/use-connectors"
import { useWorkspace } from "@/hooks/queries/use-session"
import { ApiError, errorMessage } from "@/lib/api/http"
import type { ToolSet, ToolSummary } from "@/lib/api/types"

import { ToolPicker } from "./tool-picker"

/** Enable a connector for the team, or change which of its tools the team may use (admin). */
export function TeamPolicyDialog({
  open,
  onOpenChange,
  teamId,
  slug,
  connectorId,
  displayName,
  current,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  teamId: string
  slug: string
  connectorId?: string
  displayName: string
  current: ToolSet | null
}) {
  const { isPlatformAdmin } = useWorkspace()
  const enabled = current !== null
  // The catalog needs the connector enabled for the team; before that, a platform admin can
  // list the upstream's tools with a probe, and anyone else types tool names.
  const catalog = useCatalog(teamId, slug, open && enabled)
  const probe = useProbeConnector()
  const setPolicy = useSetTeamPolicy(teamId)
  const [value, setValue] = useState<ToolSet>({ allow_all_tools: false, tools: [] })

  useEffect(() => {
    if (!open) return
    setValue(current ?? { allow_all_tools: false, tools: [] })
    if (!enabled && isPlatformAdmin && connectorId) probe.mutate(connectorId)
  }, [open])

  const tools: ToolSummary[] | null = enabled
    ? (catalog.data ?? null)
    : (probe.data?.tools ?? null)
  const loading = enabled ? catalog.isLoading : probe.isPending
  const loadError = enabled ? catalog.error : probe.error

  const save = () =>
    setPolicy.mutate(
      { slug, body: value },
      {
        onSuccess: () => {
          toast.success(enabled ? "Team tool policy updated." : `${displayName} enabled for the team.`)
          onOpenChange(false)
        },
        onError: (e) => toast.error(errorMessage(e)),
      }
    )

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{enabled ? `Team tools on ${displayName}` : `Enable ${displayName}`}</DialogTitle>
          <DialogDescription>
            Team members still need an individual grant (Team → member access) before they can
            use these tools. Admins can use every tool the team allows.
          </DialogDescription>
        </DialogHeader>
        {loading ? (
          <Skeleton className="h-56 w-full" />
        ) : (
          <>
            {loadError != null && !(loadError instanceof ApiError && loadError.status === 404) && (
              <ErrorAlert error={loadError} title="Could not list the server's tools" />
            )}
            <ToolPicker value={value} onChange={setValue} tools={tools} allLabel="All tools on this server" />
          </>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={save}
            disabled={setPolicy.isPending || (!value.allow_all_tools && !value.tools.length)}
          >
            {setPolicy.isPending && <Spinner />}
            {enabled ? "Save" : "Enable for team"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
