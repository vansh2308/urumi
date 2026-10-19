import { useMutation } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import { ErrorAlert } from "@/components/common/query-state"
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { gateway } from "@/lib/api/gateway"
import { errorMessage } from "@/lib/api/http"

import { issueSessionKey, useToolKey } from "./use-tool-key"

/**
 * The gateway only lets platform API keys (urk_) call tools, and the agent authenticates with
 * the caller's key, so chat and tool runs need one. Normally it is the login session's key,
 * issued automatically; this dialog renews it or connects a pasted key for this tab only.
 */
export function ToolKeyDialog({
  open,
  onOpenChange,
  teamId,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  teamId: string
}) {
  const { setToolKey } = useToolKey(teamId)
  const [pasted, setPasted] = useState("")
  const renew = useMutation({ mutationFn: () => issueSessionKey(teamId) })

  const verify = useMutation({
    mutationFn: async (raw: string) => {
      const me = await gateway.auth.me(raw)
      if (me.credential !== "api_key" || !me.teams.some((t) => t.team_id === teamId)) {
        throw new Error("That key does not belong to this team.")
      }
      return me
    },
  })

  const createKey = () =>
    renew.mutate(undefined, {
      onSuccess: () => {
        toast.success("Connected this login's session key.")
        onOpenChange(false)
      },
      onError: (e) => toast.error(errorMessage(e)),
    })

  const usePasted = () =>
    verify.mutate(pasted.trim(), {
      onSuccess: (me) => {
        setToolKey({
          key: pasted.trim(),
          keyId: me.api_key_id,
          prefix: pasted.trim().slice(0, 12),
          name: "Pasted key",
          expiresAt: null,
        })
        setPasted("")
        toast.success("API key connected.")
        onOpenChange(false)
      },
    })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Connect an API key</DialogTitle>
          <DialogDescription>
            Tool calls and the agent act with a platform API key, so they get exactly your
            permissions. Your conversations belong to you, not to a key, so switching keys keeps
            your chat history.
          </DialogDescription>
        </DialogHeader>
        <Tabs defaultValue="create">
          <TabsList className="w-full">
            <TabsTrigger value="create">Session key</TabsTrigger>
            <TabsTrigger value="paste">Paste existing</TabsTrigger>
          </TabsList>
          <TabsContent value="create" className="space-y-3 pt-2">
            <p className="text-sm text-muted-foreground">
              A key with the tools:read, tools:call and usage:read scopes that lives as long as
              this login and is revoked when you sign out. Issuing it again replaces the previous
              one.
            </p>
            <Button className="w-full" onClick={createKey} disabled={renew.isPending}>
              {renew.isPending && <Spinner />}
              Issue session key
            </Button>
          </TabsContent>
          <TabsContent value="paste" className="space-y-3 pt-2">
            {verify.error && <ErrorAlert error={verify.error} title="Key not accepted" />}
            <Field>
              <FieldLabel htmlFor="tool-key">API key</FieldLabel>
              <Input
                id="tool-key"
                type="password"
                autoComplete="off"
                placeholder="urk_..."
                value={pasted}
                onChange={(e) => setPasted(e.target.value)}
              />
              <FieldDescription>Needs the tools:read and tools:call scopes.</FieldDescription>
            </Field>
            <DialogFooter>
              <Button onClick={usePasted} disabled={!pasted.trim().startsWith("urk_") || verify.isPending}>
                {verify.isPending && <Spinner />}
                Verify and connect
              </Button>
            </DialogFooter>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}
