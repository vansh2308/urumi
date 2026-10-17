import { useEffect, useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Spinner } from "@/components/ui/spinner"
import { useRotateApiKey } from "@/hooks/queries/use-api-keys"
import { errorMessage } from "@/lib/api/http"
import type { ApiKey, IssuedApiKey } from "@/lib/api/types"

export function RotateKeyDialog({
  apiKey,
  teamId,
  onOpenChange,
  onIssued,
}: {
  apiKey: ApiKey | null
  teamId: string
  onOpenChange: (open: boolean) => void
  onIssued: (key: IssuedApiKey) => void
}) {
  const rotate = useRotateApiKey(teamId)
  const [grace, setGrace] = useState("0")
  useEffect(() => setGrace("0"), [apiKey])
  const graceHours = Number(grace)
  const valid = Number.isInteger(graceHours) && graceHours >= 0

  const submit = () =>
    apiKey &&
    rotate.mutate(
      { keyId: apiKey.id, graceHours },
      {
        onSuccess: (issued) => {
          onOpenChange(false)
          onIssued(issued)
        },
        onError: (e) => toast.error(errorMessage(e)),
      }
    )

  return (
    <Dialog open={apiKey !== null} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Rotate “{apiKey?.name}”</DialogTitle>
          <DialogDescription>
            Issues a new secret with the same name, scopes and restrictions. The old key stops
            working after the grace period.
          </DialogDescription>
        </DialogHeader>
        <Field data-invalid={!valid}>
          <FieldLabel htmlFor="grace">Grace period (hours)</FieldLabel>
          <Input
            id="grace"
            inputMode="numeric"
            value={grace}
            onChange={(e) => setGrace(e.target.value)}
            aria-invalid={!valid}
          />
          <FieldDescription>0 revokes the old key immediately. The gateway caps this value.</FieldDescription>
        </Field>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!valid || rotate.isPending}>
            {rotate.isPending && <Spinner />}
            Rotate key
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
