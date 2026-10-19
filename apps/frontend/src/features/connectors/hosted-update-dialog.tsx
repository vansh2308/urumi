import { zodResolver } from "@hookform/resolvers/zod"
import { useEffect } from "react"
import { Controller, useForm } from "react-hook-form"
import { toast } from "sonner"
import { z } from "zod"

import { TextField } from "@/components/common/form-fields"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "@/components/ui/field"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import { envRowSchema, toEnvRecord } from "@/features/connectors/env-vars"
import { KeyValueFields } from "@/features/connectors/key-value-fields"
import { useUpdateHosted } from "@/hooks/queries/use-connectors"
import { errorMessage } from "@/lib/api/http"
import type { HostedServer } from "@/lib/api/types"

const positiveInt = z
  .string()
  .trim()
  .refine((v) => Number.isInteger(Number(v)) && Number(v) > 0, "Whole number.")

const schema = z.object({
  image: z.string().trim().min(1).max(300),
  args: z.string().max(15000),
  env: z.array(envRowSchema).max(50),
  secret_env: z.array(envRowSchema).max(50),
  remove_secrets: z.array(z.string()),
  cpu_millicores: positiveInt,
  memory_mib: positiveInt,
  egress: z.enum(["internet", "none"]),
})
type Values = z.infer<typeof schema>

/** Change a hosted server's image, command, variables, limits or egress; the pod rolls. */
export function HostedUpdateDialog({
  server,
  open,
  onOpenChange,
}: {
  server: HostedServer
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const update = useUpdateHosted()
  const form = useForm<Values>({ resolver: zodResolver(schema) })

  useEffect(() => {
    if (open)
      form.reset({
        image: server.image,
        args: server.args.join("\n"),
        env: Object.entries(server.env).map(([key, value]) => ({ key, value })),
        secret_env: [],
        remove_secrets: [],
        cpu_millicores: String(server.cpu_millicores),
        memory_mib: String(server.memory_mib),
        egress: server.egress,
      })
  }, [open, server, form])

  const submit = form.handleSubmit((v) => {
    const secrets: Record<string, string | null> = toEnvRecord(v.secret_env)
    for (const key of v.remove_secrets) secrets[key] = null
    update.mutate(
      {
        id: server.connector.id,
        body: {
          image: v.image,
          args: v.args
            .split("\n")
            .map((a) => a.trim())
            .filter(Boolean),
          env: toEnvRecord(v.env),
          secret_env: Object.keys(secrets).length ? secrets : undefined,
          cpu_millicores: Number(v.cpu_millicores),
          memory_mib: Number(v.memory_mib),
          egress: v.egress,
        },
      },
      {
        onSuccess: () => {
          toast.success(`Updating ${server.connector.display_name}; the pod is rolling.`)
          onOpenChange(false)
        },
        onError: (e) => toast.error(errorMessage(e)),
      }
    )
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>Update deployment</DialogTitle>
            <DialogDescription>
              Changes are applied to {server.k8s_name} in {server.namespace} with a rolling update.
            </DialogDescription>
          </DialogHeader>
          <FieldGroup>
            <TextField control={form.control} name="image" label="Container image" className="font-mono" />
            <Controller
              control={form.control}
              name="args"
              render={({ field }) => (
                <Field>
                  <FieldLabel htmlFor="hosted-update-args">Arguments</FieldLabel>
                  <Textarea id="hosted-update-args" {...field} rows={3} className="font-mono text-xs" />
                  <FieldDescription>One per line.</FieldDescription>
                </Field>
              )}
            />
            <KeyValueFields control={form.control} name="env" legend="Environment variables" />
            {server.secret_env_keys.length > 0 && (
              <Controller
                control={form.control}
                name="remove_secrets"
                render={({ field }) => (
                  <FieldSet>
                    <FieldLegend variant="label">Remove secret variables</FieldLegend>
                    {server.secret_env_keys.map((key) => (
                      <Field key={key} orientation="horizontal">
                        <Checkbox
                          id={`rm-${key}`}
                          checked={field.value.includes(key)}
                          onCheckedChange={(c) =>
                            field.onChange(c ? [...field.value, key] : field.value.filter((k) => k !== key))
                          }
                        />
                        <FieldLabel htmlFor={`rm-${key}`} className="font-mono font-normal">
                          {key}
                        </FieldLabel>
                      </Field>
                    ))}
                  </FieldSet>
                )}
              />
            )}
            <KeyValueFields
              control={form.control}
              name="secret_env"
              legend="Set secret variables"
              description="New values replace existing ones with the same name. Others are kept."
              secret
            />
            <div className="grid gap-4 sm:grid-cols-3">
              <TextField control={form.control} name="cpu_millicores" label="CPU limit (millicores)" />
              <TextField control={form.control} name="memory_mib" label="Memory limit (MiB)" />
              <Controller
                control={form.control}
                name="egress"
                render={({ field }) => (
                  <Field>
                    <FieldLabel htmlFor="hosted-update-egress">Outbound network</FieldLabel>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <SelectTrigger id="hosted-update-egress" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="internet">Public internet</SelectItem>
                        <SelectItem value="none">None (DNS only)</SelectItem>
                      </SelectContent>
                    </Select>
                  </Field>
                )}
              />
            </div>
          </FieldGroup>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={update.isPending}>
              {update.isPending && <Spinner />}
              Apply
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
