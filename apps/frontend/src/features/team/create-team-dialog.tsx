import { zodResolver } from "@hookform/resolvers/zod"
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
import { useCreateTeam } from "@/hooks/queries/use-team"
import { errorMessage } from "@/lib/api/http"

const schema = z.object({ name: z.string().trim().min(1, "Enter a team name.").max(100) })

export function CreateTeamDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const create = useCreateTeam()
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { name: "" },
  })
  const submit = form.handleSubmit((values) =>
    create.mutate(values.name, {
      onSuccess: (team) => {
        toast.success(`Created ${team.name}. You are its admin.`)
        form.reset()
        onOpenChange(false)
      },
      onError: (e) => toast.error(errorMessage(e)),
    })
  )
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>Create a team</DialogTitle>
            <DialogDescription>You become the first admin of the new team.</DialogDescription>
          </DialogHeader>
          <FieldGroup>
            <TextField control={form.control} name="name" label="Team name" autoFocus />
          </FieldGroup>
          <DialogFooter>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending && <Spinner />}
              Create team
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
