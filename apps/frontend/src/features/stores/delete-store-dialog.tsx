import { useState } from "react"
import { toast } from "sonner"

import { ConfirmDialog } from "@/components/common/confirm-dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useDeleteStore } from "@/hooks/queries/use-stores"
import { errorMessage } from "@/lib/api/http"
import type { Store } from "@/lib/api/types"

/** Delete with type-the-name confirmation: it removes the store's data for good. */
export function DeleteStoreDialog({
  store,
  teamId,
  onOpenChange,
  onDeleted,
}: {
  store: Store | null
  teamId: string
  onOpenChange: (open: boolean) => void
  onDeleted?: () => void
}) {
  const remove = useDeleteStore(teamId)
  const [typed, setTyped] = useState("")
  const matches = store !== null && typed.trim() === store.name
  const close = (open: boolean) => {
    if (!open) setTyped("")
    onOpenChange(open)
  }
  return (
    <ConfirmDialog
      open={store !== null}
      onOpenChange={close}
      title={`Delete ${store?.name ?? "store"}?`}
      destructive
      confirmLabel="Delete store"
      pending={remove.isPending}
      description={
        <div className="space-y-3">
          <p>
            Its tools disappear at once. Then the namespace, WordPress and MariaDB volumes,
            secrets and the MCP server's database schema are removed. This cannot be undone.
          </p>
          <div className="space-y-1.5">
            <Label htmlFor="confirm-store-name">
              Type <span className="font-mono">{store?.name}</span> to confirm
            </Label>
            <Input
              id="confirm-store-name"
              value={typed}
              autoComplete="off"
              onChange={(e) => setTyped(e.target.value)}
            />
          </div>
        </div>
      }
      onConfirm={() => {
        if (!store || !matches) {
          toast.error("Type the store's name exactly to confirm.")
          return
        }
        remove.mutate(store.id, {
          onSuccess: () => {
            toast.success(`${store.name} is being deleted.`)
            close(false)
            onDeleted?.()
          },
          onError: (e) => toast.error(errorMessage(e)),
        })
      }}
    />
  )
}
