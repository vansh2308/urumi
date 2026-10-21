import { zodResolver } from "@hookform/resolvers/zod"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { toast } from "sonner"
import { z } from "zod"

import { TextField } from "@/components/common/form-fields"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { FieldGroup } from "@/components/ui/field"
import { Spinner } from "@/components/ui/spinner"
import { useCreateStore } from "@/hooks/queries/use-stores"
import { errorMessage } from "@/lib/api/http"
import type { CreatedStore } from "@/lib/api/types"

const schema = z.object({
  name: z.string().trim().min(1, "Enter a store name.").max(60, "At most 60 characters."),
})

export function CreateStoreDialog({
  open,
  onOpenChange,
  teamId,
  baseDomain,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  teamId: string
  baseDomain?: string
  onCreated: (store: CreatedStore) => void
}) {
  const create = useCreateStore(teamId)
  // One key per dialog opening: a double submit or a retried request returns the same store.
  const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID())
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { name: "" },
  })
  const close = (next: boolean) => {
    if (!next) {
      form.reset()
      setIdempotencyKey(crypto.randomUUID())
    }
    onOpenChange(next)
  }
  const submit = form.handleSubmit((values) =>
    create.mutate(
      { name: values.name, idempotencyKey },
      {
        onSuccess: (store) => {
          toast.success(
            store.created ? `${store.name} is being provisioned.` : `${store.name} already exists.`
          )
          close(false)
          onCreated(store)
        },
        onError: (e) => toast.error(errorMessage(e)),
      }
    )
  )
  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent>
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>Create a store</DialogTitle>
            <DialogDescription>
              A WooCommerce store in its own namespace, with its own database and MCP server.
              It is ready in a few minutes{baseDomain ? ` at <name>.${baseDomain}` : ""}; its tools
              then appear in chat as <span className="font-mono">&lt;name&gt;__list_orders</span>.
            </DialogDescription>
          </DialogHeader>
          <FieldGroup>
            <TextField control={form.control} name="name" label="Store name" placeholder="Acme" autoFocus />
          </FieldGroup>
          <DialogFooter>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending && <Spinner />}
              Create store
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
