import { useState } from "react"
import { toast } from "sonner"

import { ConfirmDialog } from "@/components/common/confirm-dialog"
import { useRemoveConnector, useRemoveTeamPolicy, useUpdateConnector } from "@/hooks/queries/use-connectors"
import { errorMessage } from "@/lib/api/http"
import type { Connector } from "@/lib/api/types"

/** Confirmation flows for disabling or deleting a connector platform-wide, or removing it from a
 *  team. `onDeleted` runs after a connector is deleted (e.g. to leave its detail page). */
export function useConnectorActions(teamId: string, onDeleted?: () => void) {
  const update = useUpdateConnector()
  const removeConnector = useRemoveConnector()
  const removePolicy = useRemoveTeamPolicy(teamId)
  const [disabling, setDisabling] = useState<Connector | null>(null)
  const [deleting, setDeleting] = useState<Connector | null>(null)
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
        description={`Every team immediately loses access to this server's tools, including running agents.${
          disabling?.kind === "hosted" ? " Its pod is scaled to zero." : ""
        } You can enable it again later.`}
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
        open={deleting !== null}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete ${deleting?.display_name}?`}
        description={
          deleting?.kind === "hosted"
            ? "The server's Deployment, Service, Secret and NetworkPolicy are deleted from the cluster, and every team's policy and grants for it are removed. Audit history is kept."
            : "The server is removed from the registry with every team's policy and grants for it. Audit history is kept."
        }
        confirmLabel="Delete server"
        destructive
        pending={removeConnector.isPending}
        onConfirm={() =>
          deleting &&
          removeConnector.mutate(deleting.id, {
            onSuccess: () => {
              toast.success(`${deleting.display_name} deleted.`)
              setDeleting(null)
              onDeleted?.()
            },
            onError: (e) => toast.error(errorMessage(e)),
          })
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
    deleteServer: (c: Connector) => setDeleting(c),
    dialogs,
  }
}
