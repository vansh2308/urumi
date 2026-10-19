import { zodResolver } from "@hookform/resolvers/zod"
import { useEffect } from "react"
import { Controller, useForm } from "react-hook-form"
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
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import {
  credentialDefaults,
  credentialSchema,
  refineCredential,
  toCredential,
} from "@/features/connectors/credential"
import { CredentialFields } from "@/features/connectors/credential-fields"
import { envRowSchema, toEnvRecord } from "@/features/connectors/env-vars"
import { KeyValueFields } from "@/features/connectors/key-value-fields"
import { useDeployHosted, useHostedSettings } from "@/hooks/queries/use-connectors"
import { errorMessage } from "@/lib/api/http"
import type { HostedServer } from "@/lib/api/types"

const wholeNumber = z
  .string()
  .trim()
  .refine((v) => v === "" || (Number.isInteger(Number(v)) && Number(v) > 0), "Whole number.")

const schema = credentialSchema
  .extend({
    slug: z
      .string()
      .trim()
      .regex(/^[a-z0-9][a-z0-9-]{0,58}$/, "Lowercase letters, digits and '-', e.g. web-search."),
    display_name: z.string().trim().min(1, "Enter a display name.").max(100),
    description: z.string().trim().max(500),
    image: z.string().trim().min(1, "Enter an image, e.g. mcp/duckduckgo:latest.").max(300),
    port: z
      .string()
      .trim()
      .refine((v) => Number.isInteger(Number(v)) && Number(v) >= 1 && Number(v) <= 65535, "1 to 65535."),
    path: z.string().trim().regex(/^\/[A-Za-z0-9._~/-]{0,199}$/, "A URL path such as /mcp."),
    args: z.string().max(15000),
    env: z.array(envRowSchema).max(50),
    secret_env: z.array(envRowSchema).max(50),
    cpu_millicores: wholeNumber,
    memory_mib: wholeNumber,
    egress: z.enum(["internet", "none"]),
  })
  .superRefine(refineCredential)
type Values = z.infer<typeof schema>

const DEFAULTS: Values = {
  slug: "",
  display_name: "",
  description: "",
  image: "",
  port: "8000",
  path: "/mcp",
  args: "",
  env: [],
  secret_env: [],
  cpu_millicores: "",
  memory_mib: "",
  egress: "internet",
  ...credentialDefaults(),
}

/** Deploy an MCP server from a container image (platform admin). The gateway creates its
 *  Deployment, Service, Secret and NetworkPolicy in the hosted namespace and registers it. */
export function HostedServerDialog({
  open,
  onOpenChange,
  onDeployed,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onDeployed?: (server: HostedServer) => void
}) {
  const settings = useHostedSettings(open)
  const deploy = useDeployHosted()
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: DEFAULTS })

  useEffect(() => {
    if (open) form.reset(DEFAULTS)
  }, [open, form])

  const submit = form.handleSubmit((v) =>
    deploy.mutate(
      {
        slug: v.slug,
        display_name: v.display_name,
        description: v.description,
        image: v.image,
        port: Number(v.port),
        path: v.path,
        args: v.args
          .split("\n")
          .map((a) => a.trim())
          .filter(Boolean),
        env: toEnvRecord(v.env),
        secret_env: toEnvRecord(v.secret_env),
        credential: toCredential(v),
        cpu_millicores: v.cpu_millicores ? Number(v.cpu_millicores) : undefined,
        memory_mib: v.memory_mib ? Number(v.memory_mib) : undefined,
        egress: v.egress,
      },
      {
        onSuccess: (server) => {
          toast.success(`Deploying ${server.connector.display_name}.`, {
            description: "It is registered now; tools appear once the pod is ready.",
          })
          onOpenChange(false)
          onDeployed?.(server)
        },
        onError: (e) => toast.error(errorMessage(e)),
      }
    )
  )

  const s = settings.data
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>Deploy MCP server from image</DialogTitle>
            <DialogDescription>
              The platform runs the image in namespace {s?.namespace ?? "…"} as a non-root pod with
              its own Secret, CPU and memory limits, and a NetworkPolicy that admits only the gateway.
              The image must speak MCP over Streamable HTTP.
            </DialogDescription>
          </DialogHeader>
          {s && !s.enabled && (
            <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
              Hosted servers are not enabled on this gateway (HOSTED_ENABLED).
            </p>
          )}
          <FieldGroup>
            <div className="grid gap-4 sm:grid-cols-2">
              <TextField
                control={form.control}
                name="slug"
                label="Slug"
                placeholder="web-search"
                description="Tool routes use it; pods are named mcp-<slug>."
              />
              <TextField control={form.control} name="display_name" label="Display name" />
            </div>
            <TextField control={form.control} name="description" label="Description" />
            <TextField
              control={form.control}
              name="image"
              label="Container image"
              placeholder="mcp/duckduckgo:latest"
              className="font-mono"
              description={
                s?.allowed_images.length
                  ? `Allowed: ${s.allowed_images.join(", ")}`
                  : "No images are allowed yet (HOSTED_ALLOWED_IMAGES)."
              }
            />
            <div className="grid gap-4 sm:grid-cols-2">
              <TextField control={form.control} name="port" label="Port" inputMode="numeric" />
              <TextField control={form.control} name="path" label="MCP path" className="font-mono" />
            </div>
            <Controller
              control={form.control}
              name="args"
              render={({ field, fieldState }) => (
                <Field data-invalid={fieldState.invalid}>
                  <FieldLabel htmlFor="hosted-args">Arguments</FieldLabel>
                  <Textarea
                    id="hosted-args"
                    {...field}
                    rows={3}
                    className="font-mono text-xs"
                    placeholder={"http\n--port\n8082"}
                  />
                  <FieldDescription>One per line. Replaces the image's default command.</FieldDescription>
                  {fieldState.invalid && <FieldError errors={[fieldState.error]} />}
                </Field>
              )}
            />
            <KeyValueFields control={form.control} name="env" legend="Environment variables" />
            <KeyValueFields
              control={form.control}
              name="secret_env"
              legend="Secret variables"
              description="Stored only in the server's Kubernetes Secret and injected as environment variables. Never shown again."
              secret
            />
            <CredentialFields control={form.control} />
            <div className="grid gap-4 sm:grid-cols-3">
              <TextField
                control={form.control}
                name="cpu_millicores"
                label="CPU limit (millicores)"
                inputMode="numeric"
                placeholder={s ? `${s.default_cpu_millicores} (max ${s.max_cpu_millicores})` : undefined}
              />
              <TextField
                control={form.control}
                name="memory_mib"
                label="Memory limit (MiB)"
                inputMode="numeric"
                placeholder={s ? `${s.default_memory_mib} (max ${s.max_memory_mib})` : undefined}
              />
              <Controller
                control={form.control}
                name="egress"
                render={({ field }) => (
                  <Field>
                    <FieldLabel htmlFor="hosted-egress">Outbound network</FieldLabel>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <SelectTrigger id="hosted-egress" className="w-full">
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
            <Button type="submit" disabled={deploy.isPending || (s !== undefined && !s.enabled)}>
              {deploy.isPending && <Spinner />}
              Deploy
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
