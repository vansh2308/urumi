import { TriangleAlertIcon } from "lucide-react"
import type { ReactNode } from "react"

import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

import { CopyButton } from "./copy-button"

/** Shows a secret exactly once. Closing the dialog drops it from memory. */
export function SecretRevealDialog({
  secret,
  title,
  description,
  children,
  notice = "Copy it now. It is stored only as a hash and cannot be shown again.",
  onClose,
}: {
  secret: string | null
  title: string
  description: ReactNode
  children?: ReactNode
  notice?: ReactNode
  onClose: () => void
}) {
  return (
    <Dialog open={secret !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg" onInteractOutside={(e) => e.preventDefault()}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <div className="flex items-center gap-2 rounded-md border bg-muted/40 p-3">
          <code className="min-w-0 flex-1 font-mono text-xs break-all select-all">{secret}</code>
          {secret && <CopyButton value={secret} label="Copy secret" />}
        </div>
        <Alert>
          <TriangleAlertIcon />
          <AlertDescription>{notice}</AlertDescription>
        </Alert>
        {children}
        <DialogFooter>
          <Button onClick={onClose}>I have copied it</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
