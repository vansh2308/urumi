import { zodResolver } from "@hookform/resolvers/zod"
import { useEffect } from "react"
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
import {
  credentialDefaults,
  credentialSchema,
  refineCredential,
  toCredential,
} from "@/features/connectors/credential"
import { CredentialFields } from "@/features/connectors/credential-fields"
import { useRegisterConnector, useUpdateConnector } from "@/hooks/queries/use-connectors"
import { errorMessage } from "@/lib/api/http"
import type { Connector } from "@/lib/api/types"

const optionalNumber = z
  .string()
  .trim()
  .refine((v) => v === "" || (!Number.isNaN(Number(v)) && Number(v) > 0), "Enter a positive number.")

const schema = credentialSchema
  .extend({
  slug: z
    .string()
    .trim()
    .regex(/^[a-z0-9][a-z0-9-]{0,62}$/, "Lowercase letters, digits and '-', e.g. woocommerce."),
  display_name: z.string().trim().min(1, "Enter a display name.").max(100),
  description: z.string().trim().max(500),
  url: z.url("Enter the server's Streamable HTTP URL, e.g. http://127.0.0.1:8000/mcp").max(500),
  timeout_seconds: optionalNumber,
  max_concurrency: optionalNumber.refine((v) => v === "" || Number.isInteger(Number(v)), "Whole number."),
  })
  .superRefine(refineCredential)
type Values = z.infer<typeof schema>

/** Register (platform admin) or edit a connector in the gateway's registry. */
export function ConnectorFormDialog({
  open,
  onOpenChange,
  connector,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  connector?: Connector
}) {
  const register = useRegisterConnector()
  const update = useUpdateConnector()
  const editing = Boolean(connector)
  const form = useForm<Values>({ resolver: zodResolver(schema) })

  useEffect(() => {
    if (open) {
      form.reset({
        slug: connector?.slug ?? "",
        display_name: connector?.display_name ?? "",
        description: connector?.description ?? "",
        url: connector?.url ?? "",
        timeout_seconds: connector?.timeout_seconds?.toString() ?? "",
        max_concurrency: connector?.max_concurrency?.toString() ?? "",
        ...credentialDefaults(connector?.credential),
      })
    }
  }, [open, connector, form])

  const pending = register.isPending || update.isPending
  const submit = form.handleSubmit((v) => {
    const body = {
      display_name: v.display_name,
      description: v.description,
      url: v.url,
      timeout_seconds: v.timeout_seconds ? Number(v.timeout_seconds) : undefined,
      max_concurrency: v.max_concurrency ? Number(v.max_concurrency) : undefined,
      credential: toCredential(v),
    }
    const done = {
      onSuccess: (c: Connector) => {
        toast.success(editing ? `Updated ${c.display_name}.` : `Registered ${c.display_name}.`)
        onOpenChange(false)
      },
      onError: (e: unknown) => toast.error(errorMessage(e)),
    }
    if (connector) {
      const { url, ...rest } = body
      update.mutate(
        {
          id: connector.id,
          // A hosted server's URL is managed by the platform.
          body: {
            ...rest,
            ...(connector.kind === "hosted" ? {} : { url }),
            clear_credential: v.credential_mode === "clear" || undefined,
          },
        },
        done
      )
    }
    else register.mutate({ slug: v.slug, ...body }, done)
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>{editing ? "Edit MCP server" : "Register MCP server"}</DialogTitle>
            <DialogDescription>
              The URL must match the gateway's UPSTREAM_ALLOWED_HOSTS allow-list. Clients never see
              it; they call tools through the gateway.
            </DialogDescription>
          </DialogHeader>
          <FieldGroup>
            <TextField
              control={form.control}
              name="slug"
              label="Slug"
              placeholder="woocommerce"
              disabled={editing}
              description="Stable identifier used in tool routes. Cannot be changed later."
            />
            <TextField control={form.control} name="display_name" label="Display name" />
            <TextField control={form.control} name="description" label="Description" />
            <TextField
              control={form.control}
              name="url"
              label="Endpoint URL"
              placeholder="http://127.0.0.1:8000/mcp"
              disabled={connector?.kind === "hosted"}
              description={
                connector?.kind === "hosted" ? "Managed by the platform for hosted servers." : undefined
              }
            />
            <CredentialFields control={form.control} existing={connector?.credential} />
            <div className="grid gap-4 sm:grid-cols-2">
              <TextField
                control={form.control}
                name="timeout_seconds"
                label="Timeout (seconds)"
                inputMode="decimal"
                placeholder="Gateway default"
              />
              <TextField
                control={form.control}
                name="max_concurrency"
                label="Max concurrency"
                inputMode="numeric"
                placeholder="Gateway default"
              />
            </div>
          </FieldGroup>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={pending}>
              {pending && <Spinner />}
              {editing ? "Save changes" : "Register"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
