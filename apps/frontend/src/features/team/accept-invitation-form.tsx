import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import { toast } from "sonner"
import { z } from "zod"

import { TextField } from "@/components/common/form-fields"
import { Button } from "@/components/ui/button"
import { FieldGroup } from "@/components/ui/field"
import { Spinner } from "@/components/ui/spinner"
import { useAcceptInvitation } from "@/hooks/queries/use-team"
import { errorMessage } from "@/lib/api/http"

const schema = z.object({
  token: z.string().trim().min(20, "Paste the full invitation token (uri_...)."),
})

export function AcceptInvitationForm({ defaultToken = "" }: { defaultToken?: string }) {
  const accept = useAcceptInvitation()
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { token: defaultToken },
  })
  const submit = form.handleSubmit((v) =>
    accept.mutate(v.token, {
      onSuccess: () => {
        toast.success("Invitation accepted. Welcome to the team.")
        form.reset({ token: "" })
      },
      onError: (e) => toast.error(errorMessage(e)),
    })
  )
  return (
    <form onSubmit={submit} className="space-y-3">
      <FieldGroup>
        <TextField
          control={form.control}
          name="token"
          label="Invitation token"
          placeholder="uri_..."
          autoComplete="off"
          description="Your account email must match the invited address."
        />
      </FieldGroup>
      <Button type="submit" variant="outline" disabled={accept.isPending}>
        {accept.isPending && <Spinner />}
        Accept invitation
      </Button>
    </form>
  )
}
