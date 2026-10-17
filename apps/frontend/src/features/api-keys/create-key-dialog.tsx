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
import {
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import { useCreateApiKey } from "@/hooks/queries/use-api-keys"
import { errorMessage } from "@/lib/api/http"
import { API_KEY_SCOPES, type ApiKeyScope, type IssuedApiKey } from "@/lib/api/types"

const SCOPE_HELP: Record<ApiKeyScope, string> = {
  "tools:read": "Discover servers and tools",
  "tools:call": "Invoke tools (REST or the MCP endpoint)",
  "usage:read": "Read your own usage records",
}

const RESTRICTION = /^[a-z0-9][a-z0-9-]{0,62}\/([A-Za-z0-9_.-]{1,128}|\*)$/

const schema = z.object({
  name: z.string().trim().min(1, "Name the key so you can recognise it later.").max(100),
  scopes: z.array(z.enum(API_KEY_SCOPES as [ApiKeyScope, ...ApiKeyScope[]])).min(1, "Pick at least one scope."),
  expires_in_days: z
    .string()
    .trim()
    .refine((v) => v === "" || (Number.isInteger(Number(v)) && Number(v) >= 1), "Whole number of days, at least 1."),
  restrictions: z
    .string()
    .refine(
      (v) =>
        v
          .split(/\n/)
          .map((s) => s.trim())
          .filter(Boolean)
          .every((s) => RESTRICTION.test(s)),
      "Each line must look like connector/tool or connector/*."
    ),
})
type Values = z.infer<typeof schema>

export function CreateKeyDialog({
  open,
  onOpenChange,
  teamId,
  maxTtlDays,
  onIssued,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  teamId: string
  maxTtlDays: number | undefined
  onIssued: (key: IssuedApiKey) => void
}) {
  const create = useCreateApiKey(teamId)
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { name: "", scopes: ["tools:read", "tools:call"], expires_in_days: "", restrictions: "" },
  })
  useEffect(() => {
    if (open) form.reset()
  }, [open, form])

  const submit = form.handleSubmit((v) => {
    const restrictions = v.restrictions
      .split(/\n/)
      .map((s) => s.trim())
      .filter(Boolean)
    create.mutate(
      {
        name: v.name,
        scopes: v.scopes,
        expires_in_days: v.expires_in_days ? Number(v.expires_in_days) : undefined,
        tool_restrictions: restrictions.length ? restrictions : undefined,
      },
      {
        onSuccess: (issued) => {
          onOpenChange(false)
          onIssued(issued)
        },
        onError: (e) => toast.error(errorMessage(e)),
      }
    )
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>Create API key</DialogTitle>
            <DialogDescription>
              The key is owned by you in this team and never grants more than your own access. The
              secret is shown once.
            </DialogDescription>
          </DialogHeader>
          <FieldGroup>
            <TextField control={form.control} name="name" label="Name" placeholder="CI pipeline" autoFocus />
            <Controller
              control={form.control}
              name="scopes"
              render={({ field, fieldState }) => (
                <FieldSet data-invalid={fieldState.invalid}>
                  <FieldLegend variant="label">Scopes</FieldLegend>
                  {API_KEY_SCOPES.map((scope) => (
                    <Field key={scope} orientation="horizontal">
                      <Checkbox
                        id={`scope-${scope}`}
                        checked={field.value.includes(scope)}
                        onCheckedChange={(c) =>
                          field.onChange(c ? [...field.value, scope] : field.value.filter((s) => s !== scope))
                        }
                      />
                      <FieldLabel htmlFor={`scope-${scope}`} className="font-normal">
                        <span className="font-mono text-xs">{scope}</span>
                        <span className="text-muted-foreground">{SCOPE_HELP[scope]}</span>
                      </FieldLabel>
                    </Field>
                  ))}
                  {fieldState.invalid && <FieldError errors={[fieldState.error]} />}
                </FieldSet>
              )}
            />
            <TextField
              control={form.control}
              name="expires_in_days"
              label="Expires in (days)"
              inputMode="numeric"
              placeholder="Platform default"
              description={maxTtlDays ? `This team allows at most ${maxTtlDays} days.` : undefined}
            />
            <Controller
              control={form.control}
              name="restrictions"
              render={({ field, fieldState }) => (
                <Field data-invalid={fieldState.invalid}>
                  <FieldLabel htmlFor="restrictions">Tool restrictions (optional)</FieldLabel>
                  <Textarea
                    id="restrictions"
                    rows={3}
                    className="font-mono text-xs"
                    placeholder={"woocommerce/list_products\nwoocommerce/*"}
                    {...field}
                    aria-invalid={fieldState.invalid}
                  />
                  <FieldDescription>One per line. Leave empty to allow all of your tools.</FieldDescription>
                  {fieldState.invalid && <FieldError errors={[fieldState.error]} />}
                </Field>
              )}
            />
          </FieldGroup>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending && <Spinner />}
              Create key
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
