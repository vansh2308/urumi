import { zodResolver } from "@hookform/resolvers/zod"
import { useEffect } from "react"
import { Controller, useForm } from "react-hook-form"
import { toast } from "sonner"
import { z } from "zod"

import { TextField } from "@/components/common/form-fields"
import { Button } from "@/components/ui/button"
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Spinner } from "@/components/ui/spinner"
import { Switch } from "@/components/ui/switch"
import { useUpdateTeam } from "@/hooks/queries/use-team"
import { errorMessage } from "@/lib/api/http"
import type { Team, UpdateTeamRequest } from "@/lib/api/types"

const positiveInt = (msg: string) =>
  z.string().trim().refine((v) => v === "" || (Number.isInteger(Number(v)) && Number(v) >= 1), msg)

const schema = z.object({
  name: z.string().trim().min(1, "Enter a team name.").max(100),
  members_can_create_keys: z.boolean(),
  max_key_ttl_days: positiveInt("Whole number of days, at least 1.").refine((v) => v !== "", "Required."),
  rate_limit_per_minute: positiveInt("Whole number, at least 1, or empty for the platform default."),
})
type Values = z.infer<typeof schema>

function toValues(team: Team): Values {
  return {
    name: team.name,
    members_can_create_keys: team.members_can_create_keys,
    max_key_ttl_days: String(team.max_key_ttl_days),
    rate_limit_per_minute: team.rate_limit_per_minute?.toString() ?? "",
  }
}

export function TeamSettingsForm({ team, editable }: { team: Team; editable: boolean }) {
  const update = useUpdateTeam(team.id)
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: toValues(team) })
  useEffect(() => form.reset(toValues(team)), [team, form])

  const submit = form.handleSubmit((v) => {
    const body: UpdateTeamRequest = {
      name: v.name,
      members_can_create_keys: v.members_can_create_keys,
      max_key_ttl_days: Number(v.max_key_ttl_days),
    }
    if (v.rate_limit_per_minute) body.rate_limit_per_minute = Number(v.rate_limit_per_minute)
    else if (team.rate_limit_per_minute !== null) body.reset_rate_limit = true
    update.mutate(body, {
      onSuccess: () => toast.success("Team settings saved."),
      onError: (e) => toast.error(errorMessage(e)),
    })
  })

  return (
    <form onSubmit={submit} className="space-y-4">
      <fieldset disabled={!editable} className="space-y-4">
        <FieldGroup>
          <TextField control={form.control} name="name" label="Team name" />
          <Controller
            control={form.control}
            name="members_can_create_keys"
            render={({ field }) => (
              <Field orientation="horizontal">
                <FieldContent>
                  <FieldLabel htmlFor="members_can_create_keys">Members can create API keys</FieldLabel>
                  <FieldDescription>Otherwise only admins issue keys.</FieldDescription>
                </FieldContent>
                <Switch
                  id="members_can_create_keys"
                  checked={field.value}
                  onCheckedChange={field.onChange}
                  disabled={!editable}
                />
              </Field>
            )}
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField
              control={form.control}
              name="max_key_ttl_days"
              label="Maximum key lifetime (days)"
              inputMode="numeric"
              description="Capped by the platform maximum."
            />
            <TextField
              control={form.control}
              name="rate_limit_per_minute"
              label="Rate limit per minute"
              inputMode="numeric"
              placeholder="Platform default"
              description="Per API key or session in this team."
            />
          </div>
        </FieldGroup>
      </fieldset>
      {editable && (
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={() => form.reset(toValues(team))} disabled={!form.formState.isDirty}>
            Discard
          </Button>
          <Button type="submit" disabled={update.isPending || !form.formState.isDirty}>
            {update.isPending && <Spinner />}
            Save changes
          </Button>
        </div>
      )}
    </form>
  )
}
