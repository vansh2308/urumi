import { useState } from "react"
import { toast } from "sonner"

import { ConfirmDialog } from "@/components/common/confirm-dialog"
import { useRemoveTeamPolicy, useUpdateConnector } from "@/hooks/queries/use-connectors"
import { errorMessage } from "@/lib/api/http"
import type { Connector } from "@/lib/api/types"

/** Confirmation flows for disabling a connector platform-wide or removing it from a team. */
export function useConnectorActions(teamId: string) {
  const update = useUpdateConnector()
  const removePolicy = useRemoveTeamPolicy(teamId)
  const [disabling, setDisabling] = useState<Connector | null>(null)
  const [removing, setRemoving] = useState<{ slug: string; name: string } | null>(null)

  const setEnabled = (c: Connector, enabled: boolean) => {
    if (!enabled) return setDisabling(c)
    update.mutate(
      { id: c.id, body: { enabled: true } },
      {
        onSuccess: () => toast.success(`${c.display_name} enabled.`),
        onError: (e) => toast.error(errorMessage(e)),
      }
    )
  }

  const dialogs = (
    <>
      <ConfirmDialog
        open={disabling !== null}
        onOpenChange={(o) => !o && setDisabling(null)}
        title={`Disable ${disabling?.display_name}?`}
        description="Every team immediately loses access to this server's tools, including running agents. You can enable it again later."
        confirmLabel="Disable server"
        destructive
        pending={update.isPending}
        onConfirm={() =>
          disabling &&
          update.mutate(
            { id: disabling.id, body: { enabled: false } },
            {
              onSuccess: () => {
                toast.success(`${disabling.display_name} disabled.`)
                setDisabling(null)
              },
              onError: (e) => toast.error(errorMessage(e)),
            }
          )
        }
      />
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(o) => !o && setRemoving(null)}
        title={`Remove ${removing?.name} from this team?`}
        description="The team policy and every member's grant for this server are deleted. Members lose access at once."
        confirmLabel="Remove from team"
        destructive
        pending={removePolicy.isPending}
        onConfirm={() =>
          removing &&
          removePolicy.mutate(removing.slug, {
            onSuccess: () => {
              toast.success(`${removing.name} removed from the team.`)
              setRemoving(null)
            },
            onError: (e) => toast.error(errorMessage(e)),
          })
        }
      />
    </>
  )

  return {
    setEnabled,
    removeFromTeam: (slug: string, name: string) => setRemoving({ slug, name }),
    dialogs,
  }
}
