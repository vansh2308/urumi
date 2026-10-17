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
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Spinner } from "@/components/ui/spinner"
import { useCreateInvitation } from "@/hooks/queries/use-team"
import { errorMessage } from "@/lib/api/http"
import type { IssuedInvitation } from "@/lib/api/types"

const schema = z.object({
  email: z.email("Enter a valid email address."),
  role: z.enum(["admin", "member"]),
})

export function InviteDialog({
  open,
  onOpenChange,
  teamId,
  onIssued,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  teamId: string
  onIssued: (invitation: IssuedInvitation) => void
}) {
  const create = useCreateInvitation(teamId)
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { email: "", role: "member" },
  })
  useEffect(() => {
    if (open) form.reset()
  }, [open, form])

  const submit = form.handleSubmit((v) =>
    create.mutate(v, {
      onSuccess: (inv) => {
        onOpenChange(false)
        onIssued(inv)
      },
      onError: (e) => toast.error(errorMessage(e)),
    })
  )
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>Invite a member</DialogTitle>
            <DialogDescription>
              The gateway does not send email: you will get a one-time token to share with the
              invitee.
            </DialogDescription>
          </DialogHeader>
          <FieldGroup>
            <TextField control={form.control} name="email" label="Email" type="email" autoFocus />
            <Controller
              control={form.control}
              name="role"
              render={({ field }) => (
                <Field>
                  <FieldLabel htmlFor="invite-role">Role</FieldLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <SelectTrigger id="invite-role" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="member">Member</SelectItem>
                      <SelectItem value="admin">Admin</SelectItem>
                    </SelectContent>
                  </Select>
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
              Create invitation
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
